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
        print("Erro: Variáveis de ambiente não configuradas.")
        return

    # --- 1. CONFIGURAÇÃO DA SESSÃO ---
    session = requests.Session()
    # User-Agent atualizado para simular navegador real
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'X-Requested-With': 'XMLHttpRequest'
    })

    login_url = "https://cbn.hptv.live/login"
    
    print("Iniciando login no painel...")
    res_get = session.get(login_url)
    soup = BeautifulSoup(res_get.text, 'html.parser')
    
    # Busca o token CSRF
    token_tag = soup.find('meta', {'name': 'csrf-token'})
    if not token_tag:
        print("Erro: Não foi possível encontrar o token CSRF. O site pode estar fora do ar.")
        return
    csrf_token = token_tag['content']

    login_data = {
        '_token': csrf_token,
        'username': cbn_user,
        'password': cbn_pass
    }
    
    # Realiza o login e verifica se deu certo
    res_login = session.post(login_url, data=login_data)
    if "dashboard" not in res_login.url and res_login.status_code != 200:
        print("Erro ao fazer login. Verifique Usuário e Senha.")
        return

    # --- 2. EXTRAÇÃO DOS DADOS (IPTV) ---
    print("Extraindo dados via AJAX...")
    ajax_url = "https://cbn.hptv.live/ajax/getClients"
    
    payload = {
        'draw': '1',
        'start': '0',
        'length': '2000',
        '_token': csrf_token
    }
    
    # Adicionamos o Referer para o servidor aceitar a requisição AJAX
    session.headers.update({'Referer': 'https://cbn.hptv.live/clients'})
    
    resp = session.post(ajax_url, data=payload)
    
    try:
        data_json = resp.json()
        raw_data = data_json['data']
    except Exception as e:
        print(f"Erro ao processar JSON: {e}")
        print("Resposta do servidor (primeiros 200 caracteres):", resp.text[:200])
        return
        
    novos_dados = []
    for item in raw_data:
        status_limpo = BeautifulSoup(item['status'], "html.parser").get_text() if '<' in item['status'] else item['status']
        
        novos_dados.append({
            'Usuario': item['username'],
            'Status': status_limpo,
            'Vencimento': item['expire'],
            'Ultima_Atualizacao': pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%d/%m/%Y %H:%M')
        })
    
    df_novo = pd.DataFrame(novos_dados)

    # --- 3. GOOGLE SHEETS ---
    print("Conectando ao Google Sheets...")
    scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets',
             "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
    
    creds_dict = json.loads(google_creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    try:
        sh = client.open("Gestao_IPTV").sheet1
    except Exception as e:
        print(f"Erro ao abrir planilha: {e}. Verifique se o nome é 'Gestao_IPTV' e se compartilhou com o e-mail da conta de serviço.")
        return
        
    existente = pd.DataFrame(sh.get_all_records())
    
    if not existente.empty:
        # Mesclar mantendo colunas Telefone e Nome_Cliente
        df_final = pd.merge(df_novo, existente[['Usuario', 'Telefone', 'Nome_Cliente']], on='Usuario', how='left')
        # Garantir que as colunas existam antes de ordenar
        for col in ['Telefone', 'Nome_Cliente']:
            if col not in df_final.columns: df_final[col] = ""
        df_final = df_final[['Usuario', 'Status', 'Vencimento', 'Telefone', 'Nome_Cliente', 'Ultima_Atualizacao']]
    else:
        df_novo['Telefone'] = ""
        df_novo['Nome_Cliente'] = ""
        df_final = df_novo[['Usuario', 'Status', 'Vencimento', 'Telefone', 'Nome_Cliente', 'Ultima_Atualizacao']]

    sh.clear()
    sh.update([df_final.columns.values.tolist()] + df_final.fillna('').values.tolist())
    print(f"Sucesso! {len(df_final)} clientes sincronizados.")

if __name__ == "__main__":
    sync_data()
