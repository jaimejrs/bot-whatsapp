import os
import json
import requests
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

    session = requests.Session()
    # User-Agent idêntico ao de um navegador real para evitar bloqueios
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'pt-BR,pt;q=0.9',
        'X-Requested-With': 'XMLHttpRequest'
    })

    # 1. ACESSAR TELA DE LOGIN PARA PEGAR O TOKEN
    print("Passo 1: Capturando Token de Login...")
    login_page = session.get("https://cbn.hptv.live/login")
    soup_login = BeautifulSoup(login_page.text, 'html.parser')
    csrf_token = soup_login.find('input', {'name': '_token'})['value']

    # 2. REALIZAR LOGIN
    print("Passo 2: Realizando Login...")
    payload_login = {
        '_token': csrf_token,
        'username': cbn_user,
        'password': cbn_pass
    }
    res_login = session.post("https://cbn.hptv.live/login", data=payload_login)
    
    if "logout" not in res_login.text.lower():
        print("Erro: Login falhou. Verifique se o usuário e senha estão corretos no Secrets.")
        return
    print("Login bem sucedido!")

    # 3. VISITAR A PÁGINA DE CLIENTES (Para validar a sessão e pegar o token de lá)
    print("Passo 3: Validando sessão na página de clientes...")
    res_clients_page = session.get("https://cbn.hptv.live/clients")
    soup_clients = BeautifulSoup(res_clients_page.text, 'html.parser')
    
    # O token CSRF pode mudar após o login, pegamos o mais recente da meta tag
    ajax_token = soup_clients.find('meta', {'name': 'csrf-token'})['content']

    # 4. EXTRAIR DADOS VIA AJAX (Onde os dados realmente estão)
    print("Passo 4: Extraindo dados dos clientes via AJAX...")
    ajax_url = "https://cbn.hptv.live/ajax/getClients"
    
    # O DataTables exige esses parâmetros para liberar a lista
    payload_ajax = {
        'draw': '1',
        'start': '0',
        'length': '2000', # Puxa até 2000 clientes de uma vez
        '_token': ajax_token
    }
    
    # Headers obrigatórios para o Laravel aceitar o AJAX
    headers_ajax = {
        'X-CSRF-TOKEN': ajax_token,
        'Referer': 'https://cbn.hptv.live/clients'
    }
    
    resp = session.post(ajax_url, data=payload_ajax, headers=headers_ajax)
    
    if resp.status_code != 200:
        print(f"Erro {resp.status_code} ao acessar API de clientes.")
        return

    raw_data = resp.json().get('data', [])
    print(f"Sucesso: {len(raw_data)} clientes encontrados.")

    # 5. TRATAR DADOS E LIMPAR HTML
    lista_final = []
    for item in raw_data:
        # O painel manda o status como HTML (<label class="...">Ativo</label>)
        # Vamos limpar isso para ficar apenas o texto
        status_limpo = BeautifulSoup(item['status'], "html.parser").get_text().strip()
        user_limpo = BeautifulSoup(item['username'], "html.parser").get_text().strip()
        
        lista_final.append({
            'Usuario': user_limpo,
            'Status': status_limpo,
            'Vencimento': item['expire'],
            'Ultima_Atualizacao': pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%d/%m/%Y %H:%M')
        })

    df_novo = pd.DataFrame(lista_final)

    # 6. ATUALIZAR GOOGLE SHEETS
    print("Passo 5: Atualizando Google Sheets...")
    scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets',
             "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
    creds_dict = json.loads(google_creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    gc = gspread.authorize(creds)
    
    try:
        sh = gc.open("Gestao_IPTV").sheet1
    except:
        print("Erro: Planilha 'Gestao_IPTV' não encontrada ou sem acesso ao e-mail da Service Account.")
        return

    existente = pd.DataFrame(sh.get_all_records())
    
    # Se a planilha já tem dados, preservamos as colunas Telefone e Nome_Cliente
    if not existente.empty and 'Usuario' in existente.columns:
        df_final = pd.merge(df_novo, existente[['Usuario', 'Telefone', 'Nome_Cliente']], on='Usuario', how='left')
    else:
        df_final = df_novo
        df_final['Telefone'] = ""
        df_final['Nome_Cliente'] = ""

    # Garantir ordem e preencher vazios
    df_final = df_final[['Usuario', 'Status', 'Vencimento', 'Telefone', 'Nome_Cliente', 'Ultima_Atualizacao']]
    df_final = df_final.fillna('')

    # Limpa e atualiza
    sh.clear()
    sh.update([df_final.columns.values.tolist()] + df_final.values.tolist())
    print("Tudo pronto! Planilha atualizada.")

if __name__ == "__main__":
    sync_data()
