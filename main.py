import os
import json
import requests
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from bs4 import BeautifulSoup

def sync_data():
    cbn_user = os.environ.get('CBN_USER')
    cbn_pass = os.environ.get('CBN_PASS')
    google_creds_json = os.environ.get('GOOGLE_CREDS')
    
    if not all([cbn_user, cbn_pass, google_creds_json]):
        print("Erro: Variáveis de ambiente não configuradas no GitHub Secrets.")
        return

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
    })

    login_url = "https://cbn.hptv.live/login"
    
    print(f"Acessando página de login: {login_url}")
    res_get = session.get(login_url)
    soup = BeautifulSoup(res_get.text, 'html.parser')
    
    token_tag = soup.find('input', {'name': '_token'}) or soup.find('meta', {'name': 'csrf-token'})
    if not token_tag:
        print("Erro: Token CSRF não encontrado.")
        return
    
    csrf_token = token_tag.get('content') or token_tag.get('value')

    # Montando o login exatamente como o formulário do site
    login_data = {
        '_token': csrf_token,
        'username': cbn_user,
        'password': cbn_pass,
        'remember': 'on'
    }
    
    print(f"Tentando logar como: {cbn_user}")
    # Enviamos o login. O site deve redirecionar.
    res_login = session.post(login_url, data=login_data, allow_redirects=True)
    
    # Verificação de sucesso procurando termos que só aparecem logado
    if "victorip" not in res_login.text.lower() and "logout" not in res_login.text.lower():
        print("Falha no login. O servidor recusou as credenciais ou pediu captcha.")
        # Se falhar, vamos ver se há algum erro visível na página
        error_soup = BeautifulSoup(res_login.text, 'html.parser')
        error_msg = error_soup.find('strong') # Comum no Laravel para mensagens de erro
        if error_msg: print(f"Mensagem do site: {error_msg.get_text()}")
        return

    print("Login realizado com sucesso!")

    # --- EXTRAÇÃO AJAX ---
    print("Extraindo lista de clientes...")
    ajax_url = "https://cbn.hptv.live/ajax/getClients"
    
    # O token para o AJAX precisa vir do header se for uma chamada via script
    headers_ajax = {
        'X-CSRF-TOKEN': csrf_token,
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': 'https://cbn.hptv.live/clients'
    }
    
    payload = {
        'draw': '1',
        'columns[0][data]': 'status',
        'start': '0',
        'length': '2000', # Tentar puxar todos
        'search[value]': '',
        '_token': csrf_token
    }
    
    resp = session.post(ajax_url, data=payload, headers=headers_ajax)
    
    try:
        raw_data = resp.json()['data']
        print(f"Total de {len(raw_data)} registros encontrados no AJAX.")
    except Exception as e:
        print(f"Erro ao processar JSON: {e}")
        return
        
    novos_dados = []
    for item in raw_data:
        # Limpando o HTML das colunas do Datatable
        status_text = BeautifulSoup(item['status'], "html.parser").get_text().strip()
        user_text = BeautifulSoup(item['username'], "html.parser").get_text().strip()
        
        novos_dados.append({
            'Usuario': user_text,
            'Status': status_text,
            'Vencimento': item['expire'],
            'Ultima_Atualizacao': pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%d/%m/%Y %H:%M')
        })
    
    df_novo = pd.DataFrame(novos_dados)

    # --- GOOGLE SHEETS ---
    print("Atualizando Planilha Google...")
    scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets',
             "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
    
    creds_dict = json.loads(google_creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    sh = client.open("Gestao_IPTV").sheet1
    existente = pd.DataFrame(sh.get_all_records())
    
    if not existente.empty:
        # Usamos o merge para não perder as colunas Telefone e Nome que você preenche no Sheets
        df_final = pd.merge(df_novo, existente[['Usuario', 'Telefone', 'Nome_Cliente']], on='Usuario', how='left')
        cols = ['Usuario', 'Status', 'Vencimento', 'Telefone', 'Nome_Cliente', 'Ultima_Atualizacao']
        for c in cols: 
            if c not in df_final.columns: df_final[c] = ""
        df_final = df_final[cols]
    else:
        df_novo['Telefone'] = ""
        df_novo['Nome_Cliente'] = ""
        df_final = df_novo[['Usuario', 'Status', 'Vencimento', 'Telefone', 'Nome_Cliente', 'Ultima_Atualizacao']]

    sh.clear()
    sh.update([df_final.columns.values.tolist()] + df_final.fillna('').values.tolist())
    print("Sincronização finalizada!")

if __name__ == "__main__":
    sync_data()
