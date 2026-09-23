import os
import time
import asyncio
import threading
import logging
import requests
from datetime import datetime, timezone, timedelta
from scipy.stats import poisson
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ==================== SERVIDOR WEB PARA O RENDER ====================
from http.server import HTTPServer, BaseHTTPRequestHandler

class SimpleHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot online!")

def rodar_servidor():
    porta = int(os.environ.get("PORT", 8080))
    servidor = HTTPServer(("0.0.0.0", porta), SimpleHandler)
    servidor.serve_forever()

threading.Thread(target=rodar_servidor, daemon=True).start()

# ==================== CONFIGURAÇÕES E CREDENCIAIS ====================
TELEGRAM_TOKEN = "8915485072:AAFAOf40E_QctAnR-U9IT4MWw74Cf5x6lUg"
TELEGRAM_CHAT_ID = 669646828
API_SPORTS_KEY = "1f5ff528f7551606e39a3d5e7607fa89"

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_SPORTS_KEY}

# Fuso horário oficial de Brasília (UTC-3)
FUSO_BR = timezone(timedelta(hours=-3))

# Ligas de interesse: Brasileirão Série A (71), B (72), Copa do Brasil (73),
# Premier League (39), La Liga (140), Serie A (135), Bundesliga (78), Champions (2), Libertadores (13)
LIGAS_ALVO = [71, 72, 73, 39, 140, 135, 78, 2, 13]

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# Armazena os IDs dos jogos já notificados para não repetir alertas
jogos_notificados_live = set()

# ==================== MODELO POISSON ====================
def calcular_poisson(media_casa=1.65, media_fora=1.10):
    prob_0x0 = poisson.pmf(0, media_casa) * poisson.pmf(0, media_fora)
    prob_1x0 = poisson.pmf(1, media_casa) * poisson.pmf(0, media_fora)
    prob_0x1 = poisson.pmf(0, media_casa) * poisson.pmf(1, media_fora)
    
    prob_under15 = prob_0x0 + prob_1x0 + prob_0x1
    prob_over15 = (1 - prob_under15) * 100
    
    return {
        "over15": round(prob_over15, 1),
        "lay_0x1": round(prob_0x1 * 100, 1)
    }

# ==================== RELATÓRIO DO DIA (/entradas) ====================
def obter_relatorio_dia():
    entradas = []
    agora_br = datetime.now(FUSO_BR)
    data_hoje = agora_br.strftime("%Y-%m-%d")
    
    try:
        url = f"{BASE_URL}/fixtures?date={data_hoje}"
        resp = requests.get(url, headers=HEADERS, timeout=10).json()
        jogos = resp.get("response", [])
        
        for item in jogos:
            liga_id = item["league"]["id"]
            if liga_id in LIGAS_ALVO:
                # Extrai o horário em formato UTC e converte para hora de Brasília
                data_utc = datetime.fromisoformat(item["fixture"]["date"].replace("Z", "+00:00"))
                data_local = data_utc.astimezone(FUSO_BR)
                hora_jogo = data_local.hour
                hora_formatada = data_local.strftime("%H:%M")
                
                # Considera apenas jogos a partir das 11h
                if hora_jogo >= 11:
                    time_casa = item["teams"]["home"]["name"]
                    time_fora = item["teams"]["away"]["name"]
                    liga_nome = item["league"]["name"]
                    
                    metricas = calcular_poisson()
                    dicas = []
                    if metricas["over15"] >= 65.0:
                        dicas.append(f"• *Over 1.5 Gols* ({metricas['over15']}%)")
                    if metricas["lay_0x1"] <= 12.0:
                        dicas.append(f"• *Lay 0x1* (Risco: {metricas['lay_0x1']}%)")
                    
                    if dicas:
                        texto = f"⚽ *{time_casa} x {time_fora}*\n🏆 {liga_nome} — 🕒 {hora_formatada}\n" + "\n".join(dicas)
                        entradas.append(texto)
    except Exception as e:
        logging.error(f"Erro ao buscar jogos do dia: {e}")

    if entradas:
        cabecalho = f"🎯 *JOGOS SELECIONADOS A PARTIR DAS 11H ({data_hoje})*\n\n"
        return cabecalho + "\n\n---\n\n".join(entradas[:12])
    
    return f"ℹ️ Nenhuma partida das ligas selecionadas cumpriu os filtros a partir das 11h hoje."

# ==================== RADAR LIVE (11:00 às 23:00 - A cada 9 minutos) ====================
async def loop_radar_live(app):
    await asyncio.sleep(10)
    while True:
        try:
            agora_br = datetime.now(FUSO_BR)
            hora_atual = agora_br.hour
            
            # Executa apenas entre 11h da manhã e 23h da noite (horário de Brasília)
            if 11 <= hora_atual <= 23:
                url = f"{BASE_URL}/fixtures?live=all"
                resp = requests.get(url, headers=HEADERS, timeout=10).json()
                jogos_aovivo = resp.get("response", [])
                
                for jogo in jogos_aovivo:
                    liga_id = jogo["league"]["id"]
                    # Filtra apenas pelas ligas de interesse para ignorar torneios fracos
                    if liga_id in LIGAS_ALVO or not LIGAS_ALVO:
                        fixture_id = jogo["fixture"]["id"]
                        minuto = jogo["fixture"]["status"]["elapsed"]
                        gols_casa = jogo["goals"]["home"] or 0
                        gols_fora = jogo["goals"]["away"] or 0
                        total_gols = gols_casa + gols_fora
                        
                        # Critério de pressão: Segundo tempo (minuto 55 a 82), com poucos gols no placar
                        if minuto and 55 <= minuto <= 82 and total_gols <= 2:
                            if fixture_id not in jogos_notificados_live:
                                time_casa = jogo["teams"]["home"]["name"]
                                time_fora = jogo["teams"]["away"]["name"]
                                liga = jogo["league"]["name"]
                                
                                alerta = (
                                    f"🔥 *ALERTA LIVE: PRESSÃO / GOL IMINENTE!*\n\n"
                                    f"⚽ *{time_casa} {gols_casa} x {gols_fora} {time_fora}*\n"
                                    f"🏆 {liga}\n"
                                    f"⏱ Minuto: *{minuto}'*\n"
                                    f"💡 *Sugestão:* Over Gols Limite\n"
                                    f"⚠️ Cenário quente no 2º tempo!"
                                )
                                await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=alerta, parse_mode="Markdown")
                                jogos_notificados_live.add(fixture_id)
                                await asyncio.sleep(2)
        except Exception as e:
            logging.error(f"Erro no Radar Live: {e}")
        
        # Intervalo de 9 minutos (540 segundos) -> Economia perfeita para cota gratuita diária
        await asyncio.sleep(540)

# ==================== COMANDOS DO TELEGRAM ====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_CHAT_ID:
        return
    await update.message.reply_text(
        "🤖 *Bot de Análises & Radar Live Ativo!*\n\n"
        "• Envie /entradas para ver os jogos filtrados do dia (a partir das 11h).\n"
        "• O *Radar Live* monitora as partidas entre 11h e 23h (a cada 9 minutos) e envia alertas automáticos de pressão no segundo tempo!",
        parse_mode="Markdown"
    )

async def cmd_entradas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_CHAT_ID:
        return
    aviso = await update.message.reply_text("🔍 Consultando a rodada de hoje e calculando métricas...")
    texto_final = obter_relatorio_dia()
    await aviso.delete()
    await update.message.reply_text(texto_final, parse_mode="Markdown")

async def post_init(application):
    asyncio.create_task(loop_radar_live(application))

# ==================== EXECUÇÃO ====================
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("entradas", cmd_entradas))
    
    print("Bot rodando com radar a cada 9 min no horário do Brasil!")
    app.run_polling()
