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

# Controle de alertas já enviados para não duplicar (separado por tempo)
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
                data_utc = datetime.fromisoformat(item["fixture"]["date"].replace("Z", "+00:00"))
                data_local = data_utc.astimezone(FUSO_BR)
                hora_jogo = data_local.hour
                hora_formatada = data_local.strftime("%H:%M")
                
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

# ==================== RADAR LIVE (11:00 às 23:00 - 1º E 2º TEMPOS) ====================
async def loop_radar_live(app):
    await asyncio.sleep(10)
    while True:
        try:
            agora_br = datetime.now(FUSO_BR)
            hora_atual = agora_br.hour
            
            # Só monitora na janela útil (11h às 23h de Brasília)
            if 11 <= hora_atual <= 23:
                url = f"{BASE_URL}/fixtures?live=all"
                resp = requests.get(url, headers=HEADERS, timeout=10).json()
                jogos_aovivo = resp.get("response", [])
                
                for jogo in jogos_aovivo:
                    liga_id = jogo["league"]["id"]
                    
                    if liga_id in LIGAS_ALVO or not LIGAS_ALVO:
                        fixture_id = jogo["fixture"]["id"]
                        minuto = jogo["fixture"]["status"]["elapsed"]
                        gols_casa = jogo["goals"]["home"] or 0
                        gols_fora = jogo["goals"]["away"] or 0
                        total_gols = gols_casa + gols_fora
                        
                        time_casa = jogo["teams"]["home"]["name"]
                        time_fora = jogo["teams"]["away"]["name"]
                        liga = jogo["league"]["name"]

                        # CENÁRIO 1: PRIMEIRO TEMPO (Minuto 20 ao 39 - Bom para Over 0.5 HT)
                        chave_1t = f"{fixture_id}_1T"
                        if minuto and 20 <= minuto <= 39 and total_gols <= 1:
                            if chave_1t not in jogos_notificados_live:
                                alerta = (
                                    f"⚡ *RADAR 1º TEMPO: OPORTUNIDADE DE GOL!*\n\n"
                                    f"⚽ *{time_casa} {gols_casa} x {gols_fora} {time_fora}*\n"
                                    f"🏆 {liga}\n"
                                    f"⏱ Minuto: *{minuto}' (1º Tempo)*\n"
                                    f"💡 *Sugestão:* Over 0.5 HT / Gol no 1º Tempo\n"
                                    f"📈 Odds do HT valorizando agora!"
                                )
                                await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=alerta, parse_mode="Markdown")
                                jogos_notificados_live.add(chave_1t)
                                await asyncio.sleep(2)

                        # CENÁRIO 2: SEGUNDO TEMPO (Minuto 58 ao 82 - Bom para Over Limite FT)
                        chave_2t = f"{fixture_id}_2T"
                        if minuto and 58 <= minuto <= 82 and total_gols <= 2:
                            if chave_2t not in jogos_notificados_live:
                                alerta = (
                                    f"🔥 *RADAR 2º TEMPO: PRESSÃO / GOL IMINENTE!*\n\n"
                                    f"⚽ *{time_casa} {gols_casa} x {gols_fora} {time_fora}*\n"
                                    f"🏆 {liga}\n"
                                    f"⏱ Minuto: *{minuto}' (2º Tempo)*\n"
                                    f"💡 *Sugestão:* Over Gols Limite / Próximo Gol FT\n"
                                    f"⚠️ Reta final com alta tendência a gol!"
                                )
                                await app.bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=alerta, parse_mode="Markdown")
                                jogos_notificados_live.add(chave_2t)
                                await asyncio.sleep(2)

        except Exception as e:
            logging.error(f"Erro no Radar Live: {e}")
        
        # Mantém a checagem a cada 9 minutos para manter a cota segura
        await asyncio.sleep(540)

# ==================== COMANDOS DO TELEGRAM ====================
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_CHAT_ID:
        return
    await update.message.reply_text(
        "🤖 *Bot de Análises & Radar Live (1T + 2T) Ativo!*\n\n"
        "• Envie /entradas para ver os jogos filtrados do dia (a partir das 11h).\n"
        "• O *Radar Live* monitora as partidas das 11h às 23h (a cada 9 minutos) e envia alertas tanto no 1º tempo (Over HT) quanto no 2º tempo (Over Limite)!",
        parse_mode="Markdown"
    )

async def cmd_entradas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_CHAT_ID:
        return
    aviso = await update.message.reply_text("🔍 Consultando a rodada de hoje e calculando métricas...")
    texto_final =  obter_relatorio_dia()
    await aviso.delete()
    await update.message.reply_text(texto_final, parse_mode="Markdown")

async def post_init(application):
    asyncio.create_task(loop_radar_live(application))

# ==================== EXECUÇÃO ====================
if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("entradas", cmd_entradas))
    
    print("Bot rodando com radar 1T e 2T a cada 9 min!")
    app.run_polling()
