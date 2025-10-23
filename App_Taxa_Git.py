import pandas as pd
import numpy as np
import streamlit as st
import datetime
from io import BytesIO
import re
import os
import requests

# URL da planilha de controle no GitHub (RAW)
CONTROL_URL = "https://raw.githubusercontent.com/bluemetrixgit/TaxaDeGestao/main/Controle%20de%20Contratos%20-%20Atualizado%202025.xlsx"

class CalculandoTaxadeGestao:
    def __init__(self):
        self.planilha_controle = None
        self.pl_data = []

    def load_control_from_github(self, broker):
        """Baixa a planilha de controle diretamente do GitHub."""
        try:
            response = requests.get(CONTROL_URL)
            response.raise_for_status()
            with BytesIO(response.content) as f:
                df = pd.read_excel(f, sheet_name=broker, skiprows=1)
            
            if broker == 'BTG':
                df['Conta'] = df['Tres'].astype(str).str.replace(r'\.0$', '', regex=True).apply(lambda x: x.zfill(9))
            elif broker in ['Safra', 'Ágora']:
                df['Conta'] = df['Conta'].astype(str).str.replace(r'\.0$', '', regex=True)
            
            df = df[
                (df['Conta'].notna()) & 
                (df['Conta'].str.strip() != '') & 
                (df['Conta'].str.len() >= 5)
            ]
            df = df[['Cliente', 'Conta', 'Taxa de Gestão']]
            df.rename(columns={'Taxa de Gestão': 'Taxa_de_Gestão', 'Conta': 'conta'}, inplace=True)
            self.planilha_controle = df
            return True
        except Exception as e:
            st.error(f"Erro ao baixar planilha do GitHub (aba {broker}): {e}")
            return False

    def load_pl_file(self, uploaded_pl, file_name, broker, year):
        """Carrega arquivo PL e extrai data do nome."""
        try:
            match = re.search(r'(\d{2}\.\d{2})\.xlsx$', file_name)
            if not match:
                st.error(f"Nome do arquivo '{file_name}' não contém data no formato 'DD.MM.xlsx'. Pulando.")
                return False
            date_str = match.group(1)
            try:
                date = datetime.datetime.strptime(f"{date_str}.{year}", '%d.%m.%Y')
            except ValueError:
                st.error(f"Data inválida em '{file_name}'. Use formato 'DD.MM.xlsx'. Pulando.")
                return False

            if broker == 'Safra':
                pl = pd.read_excel(uploaded_pl, skiprows=2)
                pl = pl[pl['Ativo'] != 'RDVT13']
                pl['PL'] = pl['PL'].astype(str).str.replace(',', '').astype(float)
                pl = pl.groupby('Conta', as_index=False)['PL'].sum()
                pl['Conta'] = pl['Conta'].astype(str).str.replace(r'\.0$', '', regex=True)
                pl = pl[
                    (pl['Conta'].notna()) & 
                    (pl['Conta'].str.strip() != '') & 
                    (pl['Conta'].str.len() >= 5)
                ]
                pl = pl[['Conta', 'PL']].rename(columns={'Conta': 'conta', 'PL': 'VALOR'})
            elif broker == 'BTG':
                pl = pd.read_excel(uploaded_pl)
                pl['Conta'] = pl['Conta'].astype(str).str.replace(r'\.0$', '', regex=True).apply(lambda x: x.zfill(9))
                pl = pl[
                    (pl['Conta'].notna()) & 
                    (pl['Conta'].str.strip() != '') & 
                    (pl['Conta'].str.len() >= 5)
                ]
                pl = pl[['Conta', 'Valor']].rename(columns={'Conta': 'conta', 'Valor': 'VALOR'})
            pl['Data'] = date
            self.pl_data.append(pl)
            return True
        except Exception as e:
            st.error(f"Erro ao carregar PL '{file_name}': {e}")
            return False

    def calculate_daily_fees(self):
        if not self.planilha_controle or self.pl_data == []:
            st.error("Planilha de controle ou arquivos PL não carregados.")
            return None, None, None

        pl_combined = pd.concat(self.pl_data, ignore_index=True)
        pl_combined = pl_combined.drop_duplicates(subset=['conta', 'Data'], keep='last')
        calculo_diario = 1/252

        tx_gestao = pd.merge(self.planilha_controle, pl_combined, on='conta', how='outer')
        tx_gestao = tx_gestao[['Cliente', 'conta', 'Taxa_de_Gestão', 'VALOR', 'Data']].dropna(subset=['conta'])

        unmatched_control = tx_gestao[tx_gestao['VALOR'].isna()]['conta'].unique()
        unmatched_pl = tx_gestao[tx_gestao['Taxa_de_Gestão'].isna()]['conta'].unique()

        tx_gestao['Tx_Gestão_Diaria'] = ((tx_gestao['Taxa_de_Gestão'] + 1) ** calculo_diario - 1) * 100
        tx_gestao['Valor_de_cobrança'] = round(tx_gestao['VALOR'] * (tx_gestao['Tx_Gestão_Diaria']) / 100, 2)
        tx_gestao['Data'] = pd.to_datetime(tx_gestao['Data']).dt.strftime('%d.%m')

        pivot_table = tx_gestao.pivot_table(
            values=['VALOR', 'Valor_de_cobrança'],
            index=['Cliente', 'conta'],
            columns='Data',
            aggfunc='first'
        ).reset_index()

        pivot_table.columns = [f"{col[1]}_{col[0]}" if col[1] else col[0] for col in pivot_table.columns]
        pivot_table = pivot_table.rename(columns=lambda x: x.replace('Valor_de_cobrança', 'Taxa'))

        dates = sorted(set(col.split('_')[0] for col in pivot_table.columns if '_' in col), key=lambda x: pd.to_datetime(x, format='%d.%m'))
        ordered_columns = ['Cliente', 'conta']
        for date in dates:
            if f"{date}_VALOR" in pivot_table.columns and f"{date}_Taxa" in pivot_table.columns:
                ordered_columns += [f"{date}_VALOR", f"{date}_Taxa"]
        pivot_table = pivot_table[ordered_columns]

        valor_cols = [c for c in pivot_table.columns if c.endswith('_VALOR')]
        taxa_cols = [c for c in pivot_table.columns if c.endswith('_Taxa')]
        pivot_table['PL_Total'] = pivot_table[valor_cols].sum(axis=1).round(2)
        pivot_table['Total_Taxa'] = pivot_table[taxa_cols].sum(axis=1).round(2)

        return pivot_table, unmatched_control, unmatched_pl

    def to_excel(self, df):
        output = BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Taxa_Gestao_Diaria')
        return output.getvalue()

def clean_currency(value):
    if isinstance(value, str):
        value = value.replace('R$', '').replace('.', '').replace(',', '.').strip()
        try:
            return float(value)
        except:
            return float('nan')
    return value

st.title("Cálculo de Taxa de Gestão Diária")

st.success("Planilha de controle carregada automaticamente do GitHub!")

st.subheader("Selecionar Tipo de Processamento")
processing_type = st.radio("Escolha a corretora:", ("BTG", "Ágora", "Safra"))

st.subheader("Selecionar Ano dos Arquivos PL")
year = st.number_input("Ano dos arquivos PL", min_value=2000, max_value=2100, value=2025, step=1)

calculadora = CalculandoTaxadeGestao()

# Carrega planilha do GitHub automaticamente
if not calculadora.load_control_from_github(processing_type):
    st.stop()

if processing_type == "BTG":
    st.subheader("Carregar Arquivos PL Diários do BTG")
    uploaded_pls = st.file_uploader("Arquivos PL (DD.MM.xlsx)", type=['xlsx'], accept_multiple_files=True, key="btg")
    if uploaded_pls:
        for pl_file in uploaded_pls:
            calculadora.load_pl_file(pl_file, pl_file.name, 'BTG', year)
        st.success("Arquivos PL do BTG carregados!")

    if st.button("Calcular Taxas Diárias do BTG"):
        result, unmatched_control, unmatched_pl = calculadora.calculate_daily_fees()
        if result is not None:
            excel_data = calculadora.to_excel(result)
            st.download_button("Baixar btg_taxa_gestao_diaria.xlsx", excel_data, "btg_taxa_gestao_diaria.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            with st.expander("Contas Não Casadas"):
                if len(unmatched_control): st.warning(f"Controle sem PL: {', '.join(unmatched_control)}")
                else: st.info("Todas as contas do controle foram casadas.")
                if len(unmatched_pl): st.warning(f"PL sem controle: {', '.join(unmatched_pl)}")
                else: st.info("Todos os PLs foram casados.")

elif processing_type == "Safra":
    st.subheader("Carregar Arquivos PL Diários do Safra")
    uploaded_pls = st.file_uploader("Arquivos PL (DD.MM.xlsx)", type=['xlsx'], accept_multiple_files=True, key="safra")
    if uploaded_pls:
        for pl_file in uploaded_pls:
            calculadora.load_pl_file(pl_file, pl_file.name, 'Safra', year)
        st.success("Arquivos PL do Safra carregados!")

    if st.button("Calcular Taxas Diárias do Safra"):
        result, unmatched_control, unmatched_pl = calculadora.calculate_daily_fees()
        if result is not None:
            excel_data = calculadora.to_excel(result)
            st.download_button("Baixar safra_taxa_gestao_diaria.xlsx", excel_data, "safra_taxa_gestao_diaria.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            with st.expander("Contas Não Casadas"):
                if len(unmatched_control): st.warning(f"Controle sem PL: {', '.join(unmatched_control)}")
                else: st.info("Todas as contas do controle foram casadas.")
                if len(unmatched_pl): st.warning(f"PL sem controle: {', '.join(unmatched_pl)}")
                else: st.info("Todos os PLs foram casados.")

elif processing_type == "Ágora":
    st.subheader("Processar PL Diários da Ágora")
    uploaded_pls_agora = st.file_uploader("Carregar Arquivos PL Diários da Ágora", type=['xlsx'], accept_multiple_files=True)

    if st.button("Gerar agora_total.xlsx") and uploaded_pls_agora:
        currency_columns = ['Ações/FIIs/ETFs/BDRs', 'Títulos privados', 'Títulos públicos', 'COE', 'Fundos e clubes de investimento', 'Opções', 'Ouro', 'Termo de Ações', 'Saldo projetado']
        dfs_agora = {}

        for pl_file in uploaded_pls_agora:
            try:
                df = pd.read_excel(pl_file, sheet_name='Sheet0')
                df = df.drop(columns=['Nome', 'CPF/CNPJ', 'Escritório', 'Barra', 'Data da Requisição'], errors='ignore')
                for col in currency_columns:
                    if col in df.columns:
                        df[col] = df[col].apply(clean_currency)
                if 'CBLC' in df.columns:
                    df['CBLC'] = df['CBLC'].astype(str).str.replace('-', '').str.replace(r'\.0$', '', regex=True)
                    df = df[df['CBLC'].str.len() >= 5]
                else:
                    st.error(f"Coluna 'CBLC' não encontrada em {pl_file.name}")
                    continue
                df['PL'] = df[currency_columns].sum(axis=1)
                df = df[['CBLC', 'PL']]
                dfs_agora[pl_file.name] = df
                st.success(f"Processado: {pl_file.name}")
            except Exception as e:
                st.error(f"Erro: {pl_file.name} → {e}")

        combined_df_agora = pd.DataFrame()
        for filename, df in dfs_agora.items():
            base = os.path.splitext(filename)[0]
            df = df.set_index('CBLC').rename(columns={'PL': f'PL_{base}'})
            combined_df_agora = combined_df_agora.combine_first(df) if not combined_df_agora.empty else df

        combined_df_agora = combined_df_agora.reset_index().drop_duplicates('CBLC')
        pl_cols = [c for c in combined_df_agora.columns if c.startswith('PL_')]
        combined_df_agora['PL Total'] = combined_df_agora[pl_cols].sum(axis=1)
        combined_df_agora.rename(columns={'CBLC': 'Conta'}, inplace=True)

        output = BytesIO()
        combined_df_agora.to_excel(output, index=False)
        output.seek(0)
        st.download_button("Baixar agora_total.xlsx", output, "agora_total.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    uploaded_agora = st.file_uploader("Carregar agora_total.xlsx", type=['xlsx'])
    if uploaded_agora:
        try:
            agora_total_df = pd.read_excel(uploaded_agora)
            agora_total_df['Conta'] = agora_total_df['Conta'].astype(str).str.lstrip('0')
            controle_agora = calculadora.planilha_controle.copy()
            controle_agora['Conta'] = controle_agora['conta'].astype(str).str.replace(r'\.0$', '', regex=True)

            merged = pd.merge(controle_agora, agora_total_df, on='Conta', how='left')
            pl_cols = [c for c in merged.columns if c.startswith('PL_') and c != 'PL Total']
            for col in pl_cols:
                date = col.replace('PL_', '')
                merged[f'Taxa_{date}'] = (merged[col] * merged['Taxa_de_Gestão'] * (1/252)).round(2)
            merged['Taxa Total'] = merged[[c for c in merged.columns if c.startswith('Taxa_')]].sum(axis=1).round(2)

            output = BytesIO()
            merged.to_excel(output, index=False)
            output.seek(0)
            st.download_button("Baixar agora_merged_with_taxa.xlsx", output, "agora_merged_with_taxa.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        except Exception as e:
            st.error(f"Erro no merge Ágora: {e}")