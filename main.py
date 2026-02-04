import asyncio

import discord
import dotenv

from llmapi import tool_loop


class MyClient(discord.Client):
    async def on_ready(self):
        print("Logged on as", self.user)

    async def on_message(self, message):
        # don't respond to ourselves
        if message.author == self.user:
            return

        m = await message.channel.send(
            "Processing your request, it may take a moment..."
        )

        async with message.channel.typing():
            # Run the blocking tool loop in a thread so typing stays active.
            result = await asyncio.to_thread(tool_loop, message.content)

        max_chunk_size = 1000
        chunks = [result[i : i + max_chunk_size] for i in range(0, len(result), max_chunk_size)] or [
            ""
        ]

        first_chunk = chunks[0]
        first_content = (
            f"{message.author.mention}\n{first_chunk}" if first_chunk else message.author.mention
        )
        await m.edit(content=first_content)

        for chunk in chunks[1:]:
            await message.channel.send(chunk)


intents = discord.Intents.default()
intents.message_content = True
client = MyClient(intents=intents)
client.run(dotenv.dotenv_values().get("discord_token"))
