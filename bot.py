import discord
from discord.ext import commands, tasks
import aiohttp
import json
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
POST_INTERVAL_MINUTES = int(os.getenv("POST_INTERVAL_MINUTES", "60"))

# ---------- Per-server config storage ----------
# Stored in server_configs.json like:
# {
#   "guild_id": {
#     "channel_id": 123456,
#     "category": "technology",
#     "api_key": "abc123"
#   }
# }

CONFIGS_FILE = "server_configs.json"
SEEN_FILE = "seen_articles.json"


def load_configs() -> dict:
    if os.path.exists(CONFIGS_FILE):
        with open(CONFIGS_FILE, "r") as f:
            return json.load(f)
    return {}


def save_configs(configs: dict):
    with open(CONFIGS_FILE, "w") as f:
        json.dump(configs, f, indent=2)


def load_seen() -> dict:
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r") as f:
            return json.load(f)
    return {}


def save_seen(seen: dict):
    with open(SEEN_FILE, "w") as f:
        json.dump(seen, f)


server_configs: dict = load_configs()
seen_articles: dict = load_seen()  # { "guild_id": ["url1", "url2", ...] }


# ---------- Helpers ----------

def get_config(guild_id: int) -> dict:
    return server_configs.get(str(guild_id), {})


def set_config(guild_id: int, key: str, value):
    gid = str(guild_id)
    if gid not in server_configs:
        server_configs[gid] = {}
    server_configs[gid][key] = value
    save_configs(server_configs)


def is_owner(interaction: discord.Interaction) -> bool:
    return interaction.user.id == interaction.guild.owner_id


CATEGORY_COLORS = {
    "business":      discord.Color.gold(),
    "entertainment": discord.Color.purple(),
    "general":       discord.Color.blurple(),
    "health":        discord.Color.green(),
    "science":       discord.Color.teal(),
    "sports":        discord.Color.orange(),
    "technology":    discord.Color.blue(),
}

CATEGORY_EMOJIS = {
    "business":      "💼",
    "entertainment": "🎬",
    "general":       "🌐",
    "health":        "❤️",
    "science":       "🔬",
    "sports":        "⚽",
    "technology":    "💻",
}

VALID_CATEGORIES = list(CATEGORY_COLORS.keys())


# ---------- News fetching ----------

async def fetch_news(api_key: str, category: str) -> list[dict]:
    url = "https://newsapi.org/v2/top-headlines"
    params = {
        "apiKey": api_key,
        "category": category,
        "language": "en",
        "pageSize": 10,
    }
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                print(f"[NewsAPI] Error {resp.status}: {await resp.text()}")
                return []
            data = await resp.json()
            return data.get("articles", [])


# ---------- Embed builder ----------

def build_embed(article: dict, category: str) -> discord.Embed:
    color = CATEGORY_COLORS.get(category, discord.Color.default())
    emoji = CATEGORY_EMOJIS.get(category, "📰")

    title = article.get("title") or "No title"
    description = article.get("description") or ""
    url = article.get("url") or ""
    image_url = article.get("urlToImage")
    source = article.get("source", {}).get("name", "Unknown source")

    embed = discord.Embed(
        title=f"{emoji}  {title}",
        description=description,
        url=url,
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"{source}  •  {category.capitalize()} news")
    if image_url:
        embed.set_image(url=image_url)

    return embed


# ---------- Bot setup ----------

intents = discord.Intents.default()
intents.guilds = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ---------- Scheduled posting ----------

@tasks.loop(minutes=POST_INTERVAL_MINUTES)
async def post_news():
    for guild in bot.guilds:
        gid = str(guild.id)
        config = server_configs.get(gid, {})

        api_key = config.get("api_key")
        channel_id = config.get("channel_id")
        category = config.get("category", "technology")

        if not api_key or not channel_id:
            continue  # Server hasn't been set up yet

        channel = bot.get_channel(int(channel_id))
        if channel is None:
            continue

        articles = await fetch_news(api_key, category)

        # Get seen list for this guild
        guild_seen = set(seen_articles.get(gid, []))
        new_count = 0

        for article in articles:
            url = article.get("url")
            if not url or url in guild_seen:
                continue

            guild_seen.add(url)
            embed = build_embed(article, category)
            try:
                await channel.send(embed=embed)
                new_count += 1
            except Exception as e:
                print(f"[Bot] Error sending to {guild.name}: {e}")

        # Save seen (keep last 500)
        seen_articles[gid] = list(guild_seen)[-500:]
        save_seen(seen_articles)

        print(f"[Bot] {guild.name} → posted {new_count} new article(s) in #{channel.name}")


@post_news.before_loop
async def before_post():
    await bot.wait_until_ready()


# ---------- Slash commands ----------

setup_group = discord.app_commands.Group(name="setup", description="Configure the news bot for your server (owner only)")


@setup_group.command(name="channel", description="Set which channel news will be posted in")
async def setup_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return

    set_config(interaction.guild.id, "channel_id", channel.id)
    await interaction.response.send_message(
        f"✅ News will now be posted in {channel.mention}!", ephemeral=True
    )


@setup_group.command(name="category", description="Set the news category")
async def setup_category(interaction: discord.Interaction, category: str):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return

    if category not in VALID_CATEGORIES:
        await interaction.response.send_message(
            f"❌ Invalid category. Choose from: {', '.join(f'`{c}`' for c in VALID_CATEGORIES)}",
            ephemeral=True,
        )
        return

    set_config(interaction.guild.id, "category", category)
    emoji = CATEGORY_EMOJIS[category]
    await interaction.response.send_message(
        f"{emoji} News category set to **{category}**!", ephemeral=True
    )


@setup_group.command(name="apikey", description="Set your own NewsAPI key (get one free at newsapi.org)")
async def setup_apikey(interaction: discord.Interaction, apikey: str):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return

    # Test the API key before saving
    await interaction.response.defer(ephemeral=True)
    articles = await fetch_news(apikey, "general")
    if not articles:
        await interaction.followup.send(
            "❌ That API key doesn't seem to work. Get a free one at https://newsapi.org/register",
            ephemeral=True,
        )
        return

    set_config(interaction.guild.id, "api_key", apikey)
    await interaction.followup.send("✅ API key saved and verified!", ephemeral=True)


@setup_group.command(name="status", description="Check your current bot configuration")
async def setup_status(interaction: discord.Interaction):
    if not is_owner(interaction):
        await interaction.response.send_message("❌ Only the server owner can do this.", ephemeral=True)
        return

    config = get_config(interaction.guild.id)
    channel_id = config.get("channel_id")
    category = config.get("category", "Not set")
    api_key = config.get("api_key")

    channel_str = f"<#{channel_id}>" if channel_id else "❌ Not set — use `/setup channel`"
    apikey_str = "✅ Set" if api_key else "❌ Not set — use `/setup apikey`"
    category_str = f"{CATEGORY_EMOJIS.get(category, '')} {category}" if category != "Not set" else "❌ Not set — use `/setup category`"

    embed = discord.Embed(title="⚙️ News Bot Configuration", color=discord.Color.blurple())
    embed.add_field(name="📢 Channel", value=channel_str, inline=False)
    embed.add_field(name="🗂️ Category", value=category_str, inline=False)
    embed.add_field(name="🔑 API Key", value=apikey_str, inline=False)
    embed.add_field(name="⏱️ Interval", value=f"Every {POST_INTERVAL_MINUTES} minutes", inline=False)

    await interaction.response.send_message(embed=embed, ephemeral=True)


bot.tree.add_command(setup_group)


@bot.tree.command(name="news", description="Get the latest news right now")
async def slash_news(interaction: discord.Interaction):
    config = get_config(interaction.guild.id)
    api_key = config.get("api_key")
    category = config.get("category", "technology")

    if not api_key:
        await interaction.response.send_message(
            "❌ This server hasn't been set up yet. The server owner needs to run `/setup apikey` first.",
            ephemeral=True,
        )
        return

    await interaction.response.defer()
    articles = await fetch_news(api_key, category)

    if not articles:
        await interaction.followup.send("No articles found right now. Try again later.")
        return

    sent = 0
    for article in articles:
        if sent >= 3:
            break
        embed = build_embed(article, category)
        await interaction.followup.send(embed=embed)
        sent += 1


# ---------- Welcome message on join ----------

@bot.event
async def on_guild_join(guild: discord.Guild):
    # Try to find a channel to send welcome message
    for channel in guild.text_channels:
        if channel.permissions_for(guild.me).send_messages:
            embed = discord.Embed(
                title="👋 Thanks for adding News Bot!",
                description=(
                    "To get started, the **server owner** needs to run these 3 commands:\n\n"
                    "1️⃣ `/setup apikey YOUR_KEY` — get a free key at https://newsapi.org/register\n"
                    "2️⃣ `/setup channel #your-channel` — choose where news gets posted\n"
                    "3️⃣ `/setup category technology` — pick your news topic\n\n"
                    "Then use `/news` anytime to get the latest news!\n"
                    "Run `/setup status` to check your configuration."
                ),
                color=discord.Color.blurple(),
            )
            await channel.send(embed=embed)
            break


# ---------- Ready ----------

@bot.event
async def on_ready():
    print(f"[Bot] Logged in as {bot.user} (ID: {bot.user.id})")
    print(f"[Bot] Active in {len(bot.guilds)} server(s)")
    try:
        synced = await bot.tree.sync()
        print(f"[Bot] Synced {len(synced)} slash command(s)")
    except Exception as e:
        print(f"[Bot] Sync error: {e}")
    post_news.start()
    print(f"[Bot] Scheduler started — every {POST_INTERVAL_MINUTES} min(s)")


bot.run(DISCORD_TOKEN)
