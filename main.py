import os
import json
import cloudscraper
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from bs4 import BeautifulSoup
import time

def sync_data():
    # 0. CONFIGURAÇÕES
    cbn_user = os.environ.get('CBN_USER')
    cbn_pass = os.environ.get('CBN_PASS')
    google_creds_json = os.environ.get('GOOGLE_CREDS')
    
    if not all([cbn_user, cbn_pass, google_creds_json]):
        print("Erro: Verifique os Secrets no GitHub.")
        return

    # Criando o scraper que pula proteções de Firewall/Cloudflare
    scraper = cloudscraper.create_scraper(
        browser={
            'browser': 'chrome',
            'platform': 'windows',
            'desktop': True
        }
    )

    # 1. PEGAR TOKEN DE LOGIN
    print("Passo 1: Abrindo página de login...")
    login_url = "https://cbn.hptv.live/login"
    res_get = scraper.get(login_url)
    soup_login = BeautifulSoup(res_get.text, 'html.parser')
    
    token_tag = soup_login.find('input', {'name': '_token'})
    if not token_tag:
        print("Erro: Não foi possível localizar o token de segurança na página.")
        return
    csrf_token = token_tag['value']

    # 2. REALIZAR LOGIN
    print(f"Passo 2: Tentando login para o usuário: {cbn_user}...")
    payload_login = {
        '_token': csrf_token,
        'username': cbn_user,
        'password': cbn_pass,
        'remember': 'on'
    }
    
    # Enviamos o login com referer (essencial)
    res_login = scraper.post(login_url, data=payload_login, headers={'Referer': login_url})
    
    # Verifica sucesso (se aparecer 'logout' no código da página, deu certo)
    if "logout" not in res_login.text.lower() and "sair" not in res_login.text.lower():
        print("Erro: Login recusado pelo servidor. Possíveis causas:")
        print("- O servidor bloqueou o IP do GitHub.")
        print("- Usuário/Senha com espaço extra no Secret.")
        # Debug para você ver o que o site respondeu
        print("Resposta do site (resumo):", res_login.text[:300].replace('\n', ' '))
        return
    
    print("Login OK! Acessando área de clientes...")

    # 3. EXTRAÇÃO AJAX
    print("Passo 3: Buscando lista de clientes via AJAX...")
    ajax_url = "https://cbn.hptv.live/ajax/getClients"
    
    headers_ajax = {
        'X-CSRF-TOKEN': csrf_token,
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': 'https://cbn.hptv.live/clients',
        'Accept': 'application/json, text/javascript, */*; q=0.01'
    }
    
    payload_ajax = {
        'draw': '1', 'start': '0', 'length': '2000', '_token': csrf_token
    }
    
    resp = scraper.post(ajax_url, data=payload_ajax, headers=headers_ajax)
    
    try:
        raw_data = resp.json().get('data', [])
        print(f"Sucesso: {len(raw_data)} clientes encontrados.")
    except Exception as e:
        print(f"Erro ao ler os dados dos clientes: {e}")
        return

    # 4. TRATAMENTO E PLANILHA
    novos_dados = []
    for item in raw_data:
        status_txt = BeautifulSoup(item['status'], "html.parser").get_text().strip()
        user_txt = BeautifulSoup(item['username'], "html.parser").get_text().strip()
        novos_dados.append({
            'Usuario': user_txt, 'Status': status_txt, 'Vencimento': item['expire'],
            'Ultima_Atualizacao': pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%d/%m/%Y %H:%M')
        })

    df_novo = pd.DataFrame(novos_dados)
    
    # Google Sheets
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

    df_final = df_final[['Usuario', 'Status', 'Vencimento', 'Telefone', 'Nome_Cliente', 'Ultima_Atualizacao']].fillna('')
    
    sh.clear()
    sh.update([df_final.columns.values.tolist()] + df_final.values.tolist())
    print("Sincronização Finalizada com Sucesso!")

if __name__ == "__main__":
    sync_data()
