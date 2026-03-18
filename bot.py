@tasks.loop(minutes=1)
async def post_news():
    now = datetime.now(timezone.utc)
    for guild in bot.guilds:
        gid = str(guild.id)
        config = server_configs.get(gid, {})
        api_key = config.get("api_key")
        channel_id = config.get("channel_id")
        category = config.get("category", "technology")
        interval_hours = float(config.get("interval_hours", 1.0))
        last_posted = config.get("last_posted", 0)
        
        print(f"[DEBUG] {guild.name}: api_key={{bool(api_key)}}, channel={{channel_id}}, interval={{interval_hours}}, last_posted={{last_posted}}")
        
        if not api_key or not channel_id:
            print(f"[DEBUG] Skipping {guild.name}: missing api_key or channel_id")
            continue
        
        time_since = (now.timestamp() - last_posted) / 3600
        print(f"[DEBUG] {guild.name}: time_since_post={{time_since:.2f}}h, interval={{interval_hours}}h")
        
        if time_since < interval_hours:
            print(f"[DEBUG] {guild.name}: Not enough time passed ({{time_since:.2f}}h < {{interval_hours}}h)")
            continue
        
        channel = bot.get_channel(int(channel_id))
        if channel is None:
            print(f"[DEBUG] {guild.name}: Channel {{channel_id}} not found")
            continue
        
        print(f"[DEBUG] {guild.name}: Fetching articles for {{category}}...")
        articles = await fetch_news(api_key, category)
        print(f"[DEBUG] {guild.name}: Got {{len(articles)}} articles from API")
        
        guild_seen = set(seen_articles.get(gid, []))
        embeds_to_send = []
        for article in articles:
            url = article.get("url")
            if not url or url in guild_seen:
                continue
            guild_seen.add(url)
            embeds_to_send.append(build_embed(article, category))
        
        print(f"[DEBUG] {guild.name}: Sending {{len(embeds_to_send)}} new articles")
        for embed in embeds_to_send:
            await channel.send(embed=embed)
        
        seen_articles[gid] = list(guild_seen)[-500:]
        save_seen(seen_articles)
        set_config(guild.id, "last_posted", now.timestamp())
        print(f"[Bot] {{guild.name}} → {{len(embeds_to_send)}} article(s)")