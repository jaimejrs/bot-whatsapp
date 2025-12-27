import os
import json
import requests
import pandas as pd
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from bs4 import BeautifulSoup

def sync_data():
    # --- CONFIGURAÇÕES VIA AMBIENTE ---
    cbn_user = os.environ.get('CBN_USER')
    cbn_pass = os.environ.get('CBN_PASS')
    google_creds_json = os.environ.get('GOOGLE_CREDS') # Conteúdo do JSON
    
    if not all([cbn_user, cbn_pass, google_creds_json]):
        print("Erro: Variáveis de ambiente não configuradas.")
        return

    # --- 1. LOGIN NO PAINEL CBN ---
    session = requests.Session()
    login_url = "https://cbn.hptv.live/login"
    
    print("Iniciando login no painel...")
    response = session.get(login_url)
    soup = BeautifulSoup(response.text, 'html.parser')
    csrf_token = soup.find('meta', {'name': 'csrf-token'})['content']

    login_data = {
        '_token': csrf_token,
        'username': cbn_user,
        'password': cbn_pass
    }
    
    session.post(login_url, data=login_data)
    
    # --- 2. EXTRAÇÃO DOS DADOS (IPTV) ---
    print("Extraindo dados via AJAX...")
    ajax_url = "https://cbn.hptv.live/ajax/getClients"
    payload = {
        'draw': '1', 'start': '0', 'length': '2000', # Puxa tudo de uma vez
        '_token': csrf_token
    }
    
    resp = session.post(ajax_url, data=payload)
    if resp.status_code != 200:
        print("Erro ao acessar dados AJAX.")
        return
        
    raw_data = resp.json()['data']
    
    # Criar lista com dados novos
    novos_dados = []
    for item in raw_data:
        # Limpeza simples do status (remover tags HTML se houver)
        status_limpo = BeautifulSoup(item['status'], "html.parser").get_text() if '<' in item['status'] else item['status']
        
        novos_dados.append({
            'Usuario': item['username'],
            'Status': status_limpo,
            'Vencimento': item['expire'],
            'Ultima_Atualizacao': pd.Timestamp.now(tz='America/Sao_Paulo').strftime('%d/%m/%Y %H:%M')
        })
    
    df_novo = pd.DataFrame(novos_dados)

    # --- 3. INTEGRAÇÃO COM GOOGLE SHEETS ---
    print("Conectando ao Google Sheets...")
    scope = ["https://spreadsheets.google.com/feeds", 'https://www.googleapis.com/auth/spreadsheets',
             "https://www.googleapis.com/auth/drive.file", "https://www.googleapis.com/auth/drive"]
    
    creds_dict = json.loads(google_creds_json)
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    sh = client.open("Gestao_IPTV").sheet1
    
    # Lógica para não apagar Telefones/Nomes manuais
    existente = pd.DataFrame(sh.get_all_records())
    
    if not existente.empty:
        # Mantém colunas manuais baseadas no 'Usuario'
        df_final = pd.merge(df_novo, existente[['Usuario', 'Telefone', 'Nome_Cliente']], on='Usuario', how='left')
        # Garante a ordem correta das colunas
        df_final = df_final[['Usuario', 'Status', 'Vencimento', 'Telefone', 'Nome_Cliente', 'Ultima_Atualizacao']]
    else:
        # Se a planilha estiver vazia, cria colunas Telefone e Nome vazias
        df_novo['Telefone'] = ""
        df_novo['Nome_Cliente'] = ""
        df_final = df_novo[['Usuario', 'Status', 'Vencimento', 'Telefone', 'Nome_Cliente', 'Ultima_Atualizacao']]

    # Atualiza a planilha (sobrescreve tudo com os dados mesclados)
    sh.clear()
    sh.update([df_final.columns.values.tolist()] + df_final.fillna('').values.tolist())
    print("Sincronização concluída com sucesso!")

if __name__ == "__main__":
    sync_data()
