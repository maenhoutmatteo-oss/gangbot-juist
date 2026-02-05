import discord
from discord.ext import commands
import aiosqlite
import os

TOKEN = os.getenv("DISCORD_TOKEN")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is niet ingesteld")

intents = discord.Intents.default()
intents.message_content = True  # nodig voor prefix commands zoals !buit
bot = commands.Bot(command_prefix="!", intents=intents)

DB_PATH = "gangbot.db"


def eur(x: float) -> str:
    # EU-notatie: 1.234,56
    s = f"{x:,.2f}"
    return "€" + s.replace(",", "X").replace(".", ",").replace("X", ".")


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS gangpot (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                totaal REAL NOT NULL
            )
        """)
        await db.execute("INSERT OR IGNORE INTO gangpot (id, totaal) VALUES (1, 0)")

        await db.execute("""
            CREATE TABLE IF NOT EXISTS rondes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                kanaal_id INTEGER NOT NULL,
                is_actief INTEGER NOT NULL,
                gestart_ts DATETIME DEFAULT CURRENT_TIMESTAMP,
                afgesloten_ts DATETIME
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS buit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ronde_id INTEGER NOT NULL,
                bedrag REAL NOT NULL,
                gangpot REAL NOT NULL,
                te_verdelen REAL NOT NULL,
                per_persoon REAL NOT NULL,
                deelnemers TEXT NOT NULL,
                kanaal_id INTEGER NOT NULL,
                ts DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.commit()

async def get_or_create_actieve_ronde(kanaal_id: int) -> int:
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "SELECT id FROM rondes WHERE kanaal_id = ? AND is_actief = 1 ORDER BY id DESC LIMIT 1",
            (kanaal_id,)
        )
        row = await cur.fetchone()
        if row:
            return int(row[0])

        await db.execute("INSERT INTO rondes (kanaal_id, is_actief) VALUES (?, 1)", (kanaal_id,))
        await db.commit()

        cur2 = await db.execute(
            "SELECT id FROM rondes WHERE kanaal_id = ? AND is_actief = 1 ORDER BY id DESC LIMIT 1",
            (kanaal_id,)
        )
        (rid,) = await cur2.fetchone()
        return int(rid)
@bot.event
async def on_ready():
    await init_db()
    print(f"{bot.user} is online!")


@bot.command()
async def gangpot(ctx):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("SELECT totaal FROM gangpot WHERE id = 1")
        (totaal,) = await cur.fetchone()
    await ctx.send(f"🏦 **Gangpot totaal:** {eur(float(totaal))}")


@bot.command()
async def buitlog(ctx):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT ts, bedrag, gangpot, te_verdelen, per_persoon, deelnemers
            FROM buit_log
            WHERE kanaal_id = ?
            ORDER BY id DESC
            LIMIT 10
        """, (ctx.channel.id,))
        rows = await cur.fetchall()

    if not rows:
        await ctx.send("📭 Geen buit entries in dit kanaal.")
        return

    lines = ["📜 **Laatste 10 buit entries (dit kanaal):**"]
    for ts, bedrag, gangpot_deel, te_verdelen, per_persoon, deelnemers in rows:
        lines.append(f"- {ts} | buit {eur(bedrag)} | gangpot {eur(gangpot_deel)} | pp {eur(per_persoon)}")
    await ctx.send("\n".join(lines))


@bot.command()
async def buit(ctx, bedrag: float, *leden: discord.Member):
    if bedrag <= 0:
        await ctx.send("❌ Bedrag moet groter zijn dan 0.")
        return

    if not leden:
        await ctx.send("❌ Je moet minstens 1 deelnemer @mentionen.\nVoorbeeld: `!buit 1000 @Jan @Piet`")
        return

    ronde_id = await get_or_create_actieve_ronde(ctx.channel.id)

    # unique deelnemers (als iemand 2x genoemd wordt)
    deelnemers = []
    seen = set()
    for m in leden:
        if m.id not in seen:
            deelnemers.append(m)
            seen.add(m.id)

    n = len(deelnemers)
    gangpot_deel = round(bedrag * 0.25, 2)
    te_verdelen = round(bedrag - gangpot_deel, 2)

    # verdeling + afrondingsverschil fixen
    per_persoon = round(te_verdelen / n, 2)
    totaal_pp = round(per_persoon * n, 2)
    verschil = round(te_verdelen - totaal_pp, 2)  # gaat naar eerste persoon

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE gangpot SET totaal = totaal + ? WHERE id = 1",
            (gangpot_deel,)
        )

        deelnemer_str = ",".join(str(m.id) for m in deelnemers)

        await db.execute("""
            INSERT INTO buit_log (
                ronde_id, bedrag, gangpot, te_verdelen,
                per_persoon, deelnemers, kanaal_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            ronde_id,
            bedrag,
            gangpot_deel,
            te_verdelen,
            per_persoon,
            deelnemer_str,
            ctx.channel.id
        ))

        await db.commit()

        cur = await db.execute("SELECT totaal FROM gangpot WHERE id = 1")
        (gangpot_totaal,) = await cur.fetchone()

    msg = []
    msg.append(f"💰 **Buit:** {eur(bedrag)}")
    msg.append(f"🏦 **25% gangpot:** {eur(gangpot_deel)} (totaal: {eur(float(gangpot_totaal))})")
    msg.append(f"🧾 **Te verdelen (75%):** {eur(te_verdelen)}")
    msg.append(f"👥 **Deelnemers ({n}):** " + ", ".join(m.mention for m in deelnemers))
    msg.append("")
    msg.append("✅ **Uitbetaling:**")

    for i, m in enumerate(deelnemers):
        extra = verschil if i == 0 else 0.0
        msg.append(f"- {m.mention}: {eur(round(per_persoon + extra, 2))}")

    await ctx.send("\n".join(msg))

@bot.command()
async def ronde(ctx):
    ronde_id = await get_or_create_actieve_ronde(ctx.channel.id)
    await ctx.send(f"🌀 **Actieve ronde (dit kanaal):** #{ronde_id}")

@bot.command()
async def stand(ctx):
    ronde_id = await get_or_create_actieve_ronde(ctx.channel.id)

    totals = {}  # user_id -> totaal bedrag

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute("""
            SELECT per_persoon, te_verdelen, deelnemers
            FROM buit_log
            WHERE kanaal_id = ? AND ronde_id = ?
            ORDER BY id ASC
        """, (ctx.channel.id, ronde_id))
        rows = await cur.fetchall()

    if not rows:
        await ctx.send("📭 Nog geen buit in deze ronde.")
        return

    for per_persoon, te_verdelen, deelnemers in rows:
        ids = [int(x) for x in deelnemers.split(",") if x.strip()]
        if not ids:
            continue

        n = len(ids)
        per_p = float(per_persoon)
        te_v = float(te_verdelen)

        totaal_pp = round(per_p * n, 2)
        verschil = round(te_v - totaal_pp, 2)  # afronding naar eerste deelnemer

        for i, uid in enumerate(ids):
            extra = verschil if i == 0 else 0.0
            totals[uid] = round(totals.get(uid, 0.0) + per_p + extra, 2)

    items = sorted(totals.items(), key=lambda x: x[1], reverse=True)
    lines = [f"📊 **Stand ronde #{ronde_id} (dit kanaal):**"]
    for uid, amt in items:
        member = ctx.guild.get_member(uid)
        name = member.mention if member else f"<@{uid}>"
        lines.append(f"- {name}: {eur(amt)}")

    await ctx.send("\n".join(lines))

@bot.command()
async def afsluiten(ctx):
    if ctx.author.id != 935496998935756861:
        await ctx.send("❌ Alleen de baas mag afsluiten.")
        return

    ronde_id = await get_or_create_actieve_ronde(ctx.channel.id)

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            UPDATE rondes
            SET is_actief = 0, afgesloten_ts = CURRENT_TIMESTAMP
            WHERE id = ? AND kanaal_id = ?
        """, (ronde_id, ctx.channel.id))

        await db.execute(
            "INSERT INTO rondes (kanaal_id, is_actief) VALUES (?, 1)",
            (ctx.channel.id,)
        )
        await db.commit()

        cur = await db.execute(
            "SELECT id FROM rondes WHERE kanaal_id = ? AND is_actief = 1 ORDER BY id DESC LIMIT 1",
            (ctx.channel.id,)
        )
        (nieuw_id,) = await cur.fetchone()

    await ctx.send(f"✅ **Ronde #{ronde_id} afgesloten.** Nieuwe ronde gestart: **#{int(nieuw_id)}**")
bot.run(TOKEN)