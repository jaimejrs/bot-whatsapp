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
        print("Erro: Secrets não configuradas.")
        return

    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    })

    # 1. PEGAR TOKEN INICIAL
    login_url = "https://cbn.hptv.live/login"
    print(f"Acessando: {login_url}")
    res_get = session.get(login_url)
    soup_login = BeautifulSoup(res_get.text, 'html.parser')
    token_login = soup_login.find('input', {'name': '_token'}).get('value')

    # 2. LOGIN
    login_data = {'_token': token_login, 'username': cbn_user, 'password': cbn_pass, 'remember': 'on'}
    print(f"Logando como: {cbn_user}")
    res_login = session.post(login_url, data=login_data, allow_redirects=True)
    
    if "logout" not in res_login.text.lower():
        print("Falha no login.")
        return
    print("Login OK!")

    # 3. VISITAR PÁGINA DE CLIENTES (Essencial para validar a sessão AJAX)
    print("Acessando página de clientes para validar sessão...")
    clients_page_url = "https://cbn.hptv.live/clients"
    res_clients = session.get(clients_page_url)
    
    # O segredo: Pegar o token que está dentro da página de clientes, pode ser diferente do login
    soup_clients = BeautifulSoup(res_clients.text, 'html.parser')
    csrf_ajax = soup_clients.find('meta', {'name': 'csrf-token'})
    if csrf_ajax:
        csrf_ajax = csrf_ajax.get('content')
    else:
        csrf_ajax = token_login # fallback

    # 4. EXTRAÇÃO AJAX (Com Headers de Navegador Real)
    print("Extraindo dados via AJAX...")
    ajax_url = "https://cbn.hptv.live/ajax/getClients"
    
    headers_ajax = {
        'X-CSRF-TOKEN': csrf_ajax,
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': clients_page_url,
        'Accept': 'application/json, text/javascript, */*; q=0.01',
    }
    
    # O payload do DataTables às vezes exige esses parâmetros para não dar erro 401/500
    payload = {
        'draw': '1',
        'start': '0',
        'length': '2000',
        '_token': csrf_ajax
    }
    
    resp = session.post(ajax_url, data=payload, headers=headers_ajax)
    
    if resp.status_code != 200:
        print(f"Erro {resp.status_code} no AJAX. Resposta: {resp.text[:100]}")
        return

    try:
        raw_data = resp.json()['data']
        print(f"Sucesso: {len(raw_data)} registros.")
    except Exception as e:
        print(f"Erro JSON: {e}")
        return
        
    # --- 5. GOOGLE SHEETS ---
    novos_dados = []
    for item in raw_data:
        # Usando 'html.parser' como fallback caso lxml falhe no ambiente
        status_text = BeautifulSoup(item['status'], "html.parser").get_text().strip()
        user_text = BeautifulSoup(item['username'], "html.parser").get_text().strip()
        novos_dados.append({
            'Usuario': user_text, 'Status': status_text, 'Vencimento': item['expire'],
            'Ultima_Atualizacao': pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%d/%m/%Y %H:%M')
        })
    
    df_novo = pd.DataFrame(novos_dados)
    creds_dict = json.loads(google_creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets', "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"])
    client = gspread.authorize(creds)
    
    sh = client.open("Gestao_IPTV").sheet1
    existente = pd.DataFrame(sh.get_all_records())
    
    if not existente.empty:
        # Se a coluna Usuario não existir no Sheets, cria
        if 'Usuario' not in existente.columns: existente['Usuario'] = ""
        df_final = pd.merge(df_novo, existente[['Usuario', 'Telefone', 'Nome_Cliente']], on='Usuario', how='left')
        cols = ['Usuario', 'Status', 'Vencimento', 'Telefone', 'Nome_Cliente', 'Ultima_Atualizacao']
        for c in cols: 
            if c not in df_final.columns: df_final[c] = ""
        df_final = df_final[cols]
    else:
        df_novo['Telefone'] = ""; df_novo['Nome_Cliente'] = ""
        df_final = df_novo[['Usuario', 'Status', 'Vencimento', 'Telefone', 'Nome_Cliente', 'Ultima_Atualizacao']]

    sh.clear()
    sh.update([df_final.columns.values.tolist()] + df_final.fillna('').values.tolist())
    print("Planilha atualizada!")

if __name__ == "__main__":
    sync_data()
