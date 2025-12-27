import os
import json
import requests
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from bs4 import BeautifulSoup
import time

def sync_data():
    cbn_user = os.environ.get('CBN_USER')
    cbn_pass = os.environ.get('CBN_PASS')
    google_creds_json = os.environ.get('GOOGLE_CREDS')
    
    if not all([cbn_user, cbn_pass, google_creds_json]):
        print("Erro: Secrets não configuradas.")
        return

    session = requests.Session()
    
    # Headers extremamente fiéis a um navegador Real
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
        'Accept-Language': 'pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7',
        'Cache-Control': 'max-age=0',
        'Sec-Ch-Ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'Sec-Ch-Ua-Mobile': '?0',
        'Sec-Ch-Ua-Platform': '"Windows"',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Upgrade-Insecure-Requests': '1'
    })

    login_url = "https://cbn.hptv.live/login"
    
    # 1. Carregar a página para ganhar o Cookie de Sessão inicial
    print("Obtendo cookies iniciais...")
    res_get = session.get(login_url)
    time.sleep(2) # Pequena pausa para simular tempo humano
    
    soup_login = BeautifulSoup(res_get.text, 'html.parser')
    token_tag = soup_login.find('input', {'name': '_token'})
    csrf_token = token_tag.get('value') if token_tag else None

    if not csrf_token:
        print("Falha ao capturar Token. O site pode estar bloqueando o IP do GitHub.")
        return

    # 2. Login
    login_data = {
        '_token': csrf_token,
        'username': cbn_user,
        'password': cbn_pass
    }
    
    print(f"Tentando logar como: {cbn_user}")
    # O segredo: o POST de login NÃO pode ter o header Sec-Fetch-Site: same-origin se vier de fora, 
    # mas o requests cuida disso. Adicionamos o referer:
    session.headers.update({'Referer': login_url})
    
    res_login = session.post(login_url, data=login_data, allow_redirects=True)
    
    # Verificação robusta: se o login falhar, o status code ou o texto nos dirão
    if "logout" not in res_login.text.lower() and "sair" not in res_login.text.lower():
        print("Falha no login.")
        # Se falhou, vamos printar o código de erro para diagnóstico
        print(f"Status Code: {res_login.status_code}")
        if "419" in str(res_login.status_code):
            print("Erro 419: Sessão expirada ou Token CSRF inválido.")
        return
    
    print("Login Realizado!")

    # 3. Extração AJAX
    print("Extraindo dados...")
    ajax_url = "https://cbn.hptv.live/ajax/getClients"
    
    # O AJAX precisa do header X-CSRF-TOKEN
    headers_ajax = {
        'X-CSRF-TOKEN': csrf_token,
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': 'https://cbn.hptv.live/clients',
        'Accept': 'application/json, text/javascript, */*; q=0.01'
    }
    
    payload = {
        'draw': '1', 'start': '0', 'length': '2000', '_token': csrf_token
    }
    
    resp = session.post(ajax_url, data=payload, headers=headers_ajax)
    
    try:
        raw_data = resp.json().get('data', [])
        print(f"Sucesso: {len(raw_data)} clientes encontrados.")
    except Exception as e:
        print(f"Erro ao processar lista: {e}")
        return

    # 4. Atualização Google Sheets
    novos_dados = []
    for item in raw_data:
        status_txt = BeautifulSoup(item['status'], "html.parser").get_text().strip()
        user_txt = BeautifulSoup(item['username'], "html.parser").get_text().strip()
        novos_dados.append({
            'Usuario': user_txt,
            'Status': status_txt,
            'Vencimento': item['expire'],
            'Ultima_Atualizacao': pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%d/%m/%Y %H:%M')
        })
    
    df_novo = pd.DataFrame(novos_dados)
    
    # Configuração Gspread
    creds_dict = json.loads(google_creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets', "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"])
    client = gspread.authorize(creds)
    
    sh = client.open("Gestao_IPTV").sheet1
    existente = pd.DataFrame(sh.get_all_records())
    
    if not existente.empty and 'Usuario' in existente.columns:
        df_final = pd.merge(df_novo, existente[['Usuario', 'Telefone', 'Nome_Cliente']], on='Usuario', how='left')
    else:
        df_final = df_novo
        df_final['Telefone'] = ""
        df_final['Nome_Cliente'] = ""

    cols = ['Usuario', 'Status', 'Vencimento', 'Telefone', 'Nome_Cliente', 'Ultima_Atualizacao']
    for c in cols:
        if c not in df_final.columns: df_final[c] = ""
    
    df_final = df_final[cols]
    
    sh.clear()
    sh.update([df_final.columns.values.tolist()] + df_final.fillna('').values.tolist())
    print("Sincronização Finalizada!")

if __name__ == "__main__":
    sync_data()
