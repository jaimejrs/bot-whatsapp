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
        print("Erro: Secrets não configuradas corretamente.")
        return

    # --- 1. CONFIGURAÇÃO DE SESSÃO COM HEADERS REAIS ---
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1'
    })

    login_url = "https://cbn.hptv.live/login"
    
    # ACESSA LOGIN PARA PEGAR COOKIES E TOKEN
    print(f"Acessando: {login_url}")
    res_get = session.get(login_url)
    soup_login = BeautifulSoup(res_get.text, 'html.parser')
    
    # Procura o token em qualquer lugar (input ou meta)
    token_tag = soup_login.find('input', {'name': '_token'})
    csrf_token = token_tag.get('value') if token_tag else None
    
    if not csrf_token:
        # Tenta pegar da meta tag se não achou no input
        meta_token = soup_login.find('meta', {'name': 'csrf-token'})
        csrf_token = meta_token.get('content') if meta_token else None

    if not csrf_token:
        print("Erro: Token CSRF não encontrado. O site pode estar sob proteção pesada.")
        return

    # --- 2. LOGIN COM PAYLOAD COMPLETO ---
    login_data = {
        '_token': csrf_token,
        'username': cbn_user,
        'password': cbn_pass,
        'remember': 'on'
    }
    
    print(f"Logando como: {cbn_user}")
    # O segredo aqui é o Referer no login
    session.headers.update({'Referer': login_url})
    res_login = session.post(login_url, data=login_data, allow_redirects=True)
    
    # Verifica sucesso de forma ampla
    if "logout" not in res_login.text.lower() and "sair" not in res_login.text.lower():
        print("Falha no login. Verifique se as credenciais no Secrets estão sem espaços.")
        return
    print("Login realizado!")

    # --- 3. PÁGINA DE CLIENTES ---
    clients_page_url = "https://cbn.hptv.live/clients"
    session.get(clients_page_url) # "Esquenta" a sessão na página alvo

    # --- 4. EXTRAÇÃO AJAX ---
    print("Chamando API de clientes...")
    ajax_url = "https://cbn.hptv.live/ajax/getClients"
    
    # Headers para simular a requisição do DataTable
    headers_ajax = {
        'X-CSRF-TOKEN': csrf_token,
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': clients_page_url,
        'Accept': 'application/json, text/javascript, */*; q=0.01'
    }
    
    payload = {
        'draw': '1',
        'start': '0',
        'length': '2000', # Puxa todos
        '_token': csrf_token
    }
    
    resp = session.post(ajax_url, data=payload, headers=headers_ajax)
    
    if resp.status_code != 200:
        print(f"Erro {resp.status_code} no AJAX.")
        return

    try:
        raw_data = resp.json().get('data', [])
        print(f"Capturados {len(raw_data)} clientes.")
    except:
        print("Erro ao ler JSON do servidor.")
        return
        
    # --- 5. GOOGLE SHEETS ---
    novos_dados = []
    for item in raw_data:
        # Limpeza rápida de HTML
        s_soup = BeautifulSoup(item['status'], "html.parser")
        u_soup = BeautifulSoup(item['username'], "html.parser")
        
        novos_dados.append({
            'Usuario': u_soup.get_text().strip(),
            'Status': s_soup.get_text().strip(),
            'Vencimento': item['expire'],
            'Ultima_Atualizacao': pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%d/%m/%Y %H:%M')
        })
    
    df_novo = pd.DataFrame(novos_dados)
    
    # Conexão Sheets
    creds_dict = json.loads(google_creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets', "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"])
    client = gspread.authorize(creds)
    
    sh = client.open("Gestao_IPTV").sheet1
    existente = pd.DataFrame(sh.get_all_records())
    
    # Merge para não perder Telefone e Nome
    if not existente.empty:
        df_final = pd.merge(df_novo, existente[['Usuario', 'Telefone', 'Nome_Cliente']], on='Usuario', how='left')
        for c in ['Telefone', 'Nome_Cliente']: 
            if c not in df_final.columns: df_final[c] = ""
    else:
        df_novo['Telefone'] = ""; df_novo['Nome_Cliente'] = ""
        df_final = df_novo

    # Reordena e limpa
    df_final = df_final[['Usuario', 'Status', 'Vencimento', 'Telefone', 'Nome_Cliente', 'Ultima_Atualizacao']]
    sh.clear()
    sh.update([df_final.columns.values.tolist()] + df_final.fillna('').values.tolist())
    print("Planilha atualizada com sucesso!")

if __name__ == "__main__":
    sync_data()
