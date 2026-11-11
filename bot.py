import os
import json
import sqlite3
import asyncio
import random

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler,
    CallbackQueryHandler, ContextTypes
)
from google import genai

BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

ai = genai.Client(api_key=GEMINI_API_KEY)

db = sqlite3.connect("quiz.db", check_same_thread=False)

db.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    name TEXT,
    score INTEGER DEFAULT 0
)
""")

db.execute("""
CREATE TABLE IF NOT EXISTS mistakes (
    user_id INTEGER,
    subject TEXT,
    topic TEXT
)
""")

db.commit()

SUBJECTS = [
    "गणित",
    "सामान्य विज्ञान",
    "रीजनिंग",
    "सामान्य ज्ञान"
]

async def generate_question(subject, topic=""):
    prompt = f"""
    Railway Group D CEN के लिए हिंदी में एक MCQ बनाओ।
    विषय: {subject}
    टॉपिक: {topic or 'कोई भी परीक्षा उपयोगी टॉपिक'}
    स्तर: 10वीं कक्षा और Group D परीक्षा।

    केवल JSON दो:
    {{
      "topic": "टॉपिक",
      "question": "प्रश्न",
      "A": "विकल्प A",
      "B": "विकल्प B",
      "C": "विकल्प C",
      "D": "विकल्प D",
      "answer": "A",
      "explanation": "सरल हिंदी में व्याख्या"
    }}
    """

    response = await asyncio.to_thread(
        ai.models.generate_content,
        model="gemini-2.5-flash",
        contents=prompt,
        config={"response_mime_type": "application/json"}
    )

    return json.loads(response.text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user

    db.execute(
        "INSERT OR IGNORE INTO users (user_id, name) VALUES (?, ?)",
        (user.id, user.first_name)
    )
    db.commit()

    keyboard = [
        [InlineKeyboardButton("📚 Maths", callback_data="subject:गणित"),
         InlineKeyboardButton("🔬 Science", callback_data="subject:सामान्य विज्ञान")],
        [InlineKeyboardButton("🧠 Reasoning", callback_data="subject:रीजनिंग"),
         InlineKeyboardButton("🌍 GK", callback_data="subject:सामान्य ज्ञान")],
        [InlineKeyboardButton("📊 My Score", callback_data="score"),
         InlineKeyboardButton("🏆 Leaderboard", callback_data="top")],
        [InlineKeyboardButton("🎯 Weak Topic Practice", callback_data="weak")]
    ]

    await update.message.reply_text(
        f"🚆 स्वागत है {user.first_name}!\n\n"
        "RAILWAY GROUP D QUIZ MASTER\n\n"
        "अपना विषय चुनें और Quiz शुरू करें।",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def show_question(query, context, subject, topic=""):
    await query.edit_message_text("⏳ AI आपके लिए प्रश्न तैयार कर रहा है...")

    try:
        q = await generate_question(subject, topic)

        context.user_data["question"] = q
        context.user_data["subject"] = subject

        keyboard = [
            [InlineKeyboardButton(f"A. {q['A']}", callback_data="answer:A")],
            [InlineKeyboardButton(f"B. {q['B']}", callback_data="answer:B")],
            [InlineKeyboardButton(f"C. {q['C']}", callback_data="answer:C")],
            [InlineKeyboardButton(f"D. {q['D']}", callback_data="answer:D")]
        ]

        await query.edit_message_text(
            f"📚 विषय: {subject}\n"
            f"📖 टॉपिक: {q['topic']}\n\n"
            f"❓ {q['question']}",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    except Exception:
        await query.edit_message_text(
            "प्रश्न बनाने में समस्या हुई। कृपया दोबारा कोशिश करें।"
        )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data

    if data.startswith("subject:"):
        subject = data.split(":", 1)[1]
        await show_question(query, context, subject)
        return

    if data == "score":
        row = db.execute(
            "SELECT score FROM users WHERE user_id=?",
            (query.from_user.id,)
        ).fetchone()

        score = row[0] if row else 0
        await query.edit_message_text(f"📊 आपका कुल Score: {score}")
        return

    if data == "top":
        rows = db.execute(
            "SELECT name, score FROM users ORDER BY score DESC LIMIT 10"
        ).fetchall()

        result = "🏆 TOP 10 LEADERBOARD\n\n"

        for i, row in enumerate(rows, 1):
            result += f"{i}. {row[0]} — {row[1]} अंक\n"

        await query.edit_message_text(result)
        return

    if data == "weak":
        row = db.execute("""
            SELECT subject, topic, COUNT(*) as total
            FROM mistakes
            WHERE user_id=?
            GROUP BY subject, topic
            ORDER BY total DESC LIMIT 1
        """, (query.from_user.id,)).fetchone()

        if not row:
            await query.edit_message_text(
                "अभी आपकी कोई Weak Topic नहीं मिली। पहले Quiz खेलें।"
            )
            return

        await show_question(query, context, row[0], row[1])
        return

    if data.startswith("answer:"):
        q = context.user_data.get("question")

        if not q:
            await query.edit_message_text("कृपया नया Quiz शुरू करें।")
            return

        selected = data.split(":")[1]
        correct = q["answer"].upper()
        subject = context.user_data["subject"]

        if selected == correct:
            db.execute(
                "UPDATE users SET score=score+1 WHERE user_id=?",
                (query.from_user.id,)
            )
            result = "✅ बिल्कुल सही! आपको 1 अंक मिला।"
        else:
            db.execute(
                "INSERT INTO mistakes VALUES (?, ?, ?)",
                (query.from_user.id, subject, q["topic"])
            )
            result = f"❌ आपका उत्तर गलत है। सही उत्तर: {correct}"

        db.commit()

        await query.edit_message_text(
            f"{result}\n\n"
            f"📖 व्याख्या:\n{q['explanation']}\n\n"
            "अगले प्रश्न के लिए /quiz लिखें।"
        )


async def quiz(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton(s, callback_data=f"subject:{s}")]
        for s in SUBJECTS
    ]

    await update.message.reply_text(
        "अपना विषय चुनें:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("quiz", quiz))
    app.add_handler(CallbackQueryHandler(buttons))

    print("Railway Group D Bot Started!")

    app.run_polling()


if __name__ == "__main__":
    main()
