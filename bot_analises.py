import os
import threading
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
import logging
import requests
from datetime import datetime, timedelta
from scipy.stats import poisson
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# ==================== CREDENCIAIS CONFIGURADAS ====================
TELEGRAM_TOKEN = "8915485072:AAFAOf40E_QctAnR-U9IT4MWw74Cf5x6lUg"
TELEGRAM_CHAT_ID = 669646828
API_SPORTS_KEY = "1f5ff528f7551606e39a3d5e7607fa89"
# ==================================================================

BASE_URL = "https://v3.football.api-sports.io"
HEADERS = {"x-apisports-key": API_SPORTS_KEY}

LIGAS_ALVO = [39, 40, 71, 72, 140, 135, 119, 244, 179, 13, 11]

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

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

def obter_relatorio():
    ano_atual = datetime.now().year
    entradas_selecionadas = []
    
    datas_para_analisar = [
        (datetime.now() + timedelta(days=d)).strftime("%Y-%m-%d")
        for d in range(4)
    ]
    
    for data_jogo in datas_para_analisar:
        for liga_id in LIGAS_ALVO:
            try:
                params = {"date": data_jogo, "league": liga_id, "season": ano_atual}
                resposta = requests.get(f"{BASE_URL}/fixtures", headers=HEADERS, params=params, timeout=10).json()
                jogos = resposta.get("response", [])
                
                for item in jogos:
                    time_casa = item["teams"]["home"]["name"]
                    time_fora = item["teams"]["away"]["name"]
                    liga_nome = item["league"]["name"]
                    hora = item["fixture"]["date"][11:16]
                    data_formatada = f"{data_jogo[8:10]}/{data_jogo[5:7]}"
                    
                    metricas = calcular_poisson()
                    
                    dicas = []
                    if metricas["over15"] >= 75.0:
                        dicas.append(f"• *Over 1.5 Golos* ({metricas['over15']}%)")
                    if metricas["lay_0x1"] <= 7.0:
                        dicas.append(f"• *Lay 0x1* (Risco: {metricas['lay_0x1']}%)")
                    
                    if dicas:
                        texto_jogo = f"⚽ *{time_casa} x {time_fora}*\n🏆 {liga_nome} — 📅 {data_formatada} 🕒 {hora}\n" + "\n".join(dicas)
                        entradas_selecionadas.append(texto_jogo)
            except Exception:
                continue

    if entradas_selecionadas:
        cabecalho = "🎯 *MELHORES ENTRADAS SELECIONADAS*\n\n"
        return cabecalho + "\n\n---\n\n".join(entradas_selecionadas[:15])
    return "ℹ️ Nenhuma partida atendeu aos filtros nos próximos dias."

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_CHAT_ID:
        return
    await update.message.reply_text("Olá! Envie /entradas a qualquer momento para analisar os jogos.")

async def cmd_entradas(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != TELEGRAM_CHAT_ID:
        return
    aviso = await update.message.reply_text("🔍 A consultar a API e a calcular probabilidades... aguarde alguns segundos.")
    texto_final = obter_relatorio()
    await aviso.delete()
    await update.message.reply_text(texto_final, parse_mode="Markdown")

if __name__ == "__main__":
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("entradas", cmd_entradas))
    
    print("Bot em execução no Telegram... Pode enviar comandos diretamente pela conversa!")
    app.run_polling()
