import os
import json
import requests
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from bs4 import BeautifulSoup

def sync_data():
    # --- 0. CONFIGURAÇÕES E CREDENCIAIS ---
    cbn_user = os.environ.get('CBN_USER')
    cbn_pass = os.environ.get('CBN_PASS')
    google_creds_json = os.environ.get('GOOGLE_CREDS')
    
    if not all([cbn_user, cbn_pass, google_creds_json]):
        print("Erro: Variáveis de ambiente (Secrets) não configuradas.")
        return

    # --- 1. CONFIGURAÇÃO DA SESSÃO ---
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
    
    # Captura o Token CSRF do formulário
    token_tag = soup.find('input', {'name': '_token'}) or soup.find('meta', {'name': 'csrf-token'})
    if not token_tag:
        print("Erro: Token CSRF não encontrado.")
        return
    
    csrf_token = token_tag.get('content') or token_tag.get('value')

    # --- 2. REALIZAR LOGIN ---
    login_data = {
        '_token': csrf_token,
        'username': cbn_user,
        'password': cbn_pass,
        'remember': 'on'
    }
    
    print(f"Tentando logar como: {cbn_user}")
    res_login = session.post(login_url, data=login_data, allow_redirects=True)
    
    if "logout" not in res_login.text.lower() and cbn_user.lower() not in res_login.text.lower():
        print("Falha no login. Verifique as credenciais ou se o site mudou.")
        return

    print("Login realizado com sucesso!")

    # --- 3. EXTRAÇÃO DOS DADOS VIA AJAX ---
    print("Extraindo lista de clientes via AJAX...")
    ajax_url = "https://cbn.hptv.live/ajax/getClients"
    
    # Headers específicos para a chamada AJAX ser aceita pelo Laravel/DataTables
    headers_ajax = {
        'X-CSRF-TOKEN': csrf_token,
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': 'https://cbn.hptv.live/clients',
        'Accept': 'application/json, text/javascript, */*; q=0.01'
    }
    
    payload = {
        'draw': '1',
        'start': '0',
        'length': '2000', # Puxa até 2000 registros
        '_token': csrf_token
    }
    
    resp = session.post(ajax_url, data=payload, headers=headers_ajax)
    
    if resp.status_code != 200:
        print(f"Erro no servidor AJAX: Código {resp.status_code}")
        print("Resposta parcial:", resp.text[:300])
        return

    try:
        json_resp = resp.json()
        raw_data = json_resp.get('data', [])
        print(f"Total de {len(raw_data)} clientes encontrados.")
    except Exception as e:
        print(f"Erro ao processar JSON: {e}")
        print("Conteúdo da resposta não é um JSON válido.")
        return
        
    # --- 4. TRATAMENTO DOS DADOS COM PANDAS ---
    novos_dados = []
    for item in raw_data:
        # Extrai apenas o texto das tags HTML que o Datatables envia
        status_text = BeautifulSoup(item['status'], "lxml").get_text().strip()
        user_text = BeautifulSoup(item['username'], "lxml").get_text().strip()
        
        novos_dados.append({
            'Usuario': user_text,
            'Status': status_text,
            'Vencimento': item['expire'],
            'Ultima_Atualizacao': pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%d/%m/%Y %H:%M')
        })
    
    df_novo = pd.DataFrame(novos_dados)

    # --- 5. ATUALIZAÇÃO NO GOOGLE SHEETS ---
    print("Conectando ao Google Sheets...")
    scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets',
             "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
    
    creds_dict = json.loads(google_creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    # Certifique-se que o nome da planilha é exatamente "Gestao_IPTV"
    sh = client.open("Gestao_IPTV").sheet1
    
    # Lê dados existentes para preservar as colunas manuais (Telefone e Nome)
    existente = pd.DataFrame(sh.get_all_records())
    
    if not existente.empty:
        # Cruza os dados novos com os antigos para manter Telefone e Nome_Cliente
        df_final = pd.merge(df_novo, existente[['Usuario', 'Telefone', 'Nome_Cliente']], on='Usuario', how='left')
        cols = ['Usuario', 'Status', 'Vencimento', 'Telefone', 'Nome_Cliente', 'Ultima_Atualizacao']
        # Garante que todas as colunas existam
        for c in cols:
            if c not in df_final.columns: df_final[c] = ""
        df_final = df_final[cols]
    else:
        df_novo['Telefone'] = ""
        df_novo['Nome_Cliente'] = ""
        df_final = df_novo[['Usuario', 'Status', 'Vencimento', 'Telefone', 'Nome_Cliente', 'Ultima_Atualizacao']]

    # Limpa e atualiza a planilha de uma vez
    sh.clear()
    sh.update([df_final.columns.values.tolist()] + df_final.fillna('').values.tolist())
    
    print(f"Sucesso! Planilha atualizada com {len(df_final)} linhas.")

if __name__ == "__main__":
    sync_data()
