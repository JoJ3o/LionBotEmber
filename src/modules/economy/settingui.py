import asyncio

import discord
from discord.ui.select import select, Select, ChannelSelect
from discord.ui.button import button, Button, ButtonStyle

from meta import LionBot

from utils.ui import ConfigUI, DashboardSection
from utils.lib import MessageArgs

from .settings import EconomySettings
from . import babel

_p = babel._p


class EconomyConfigUI(ConfigUI):
    setting_classes = (
        EconomySettings.StartingFunds,
        EconomySettings.CoinsPerXP,
        EconomySettings.AllowTransfers,
    )

    def __init__(self, bot: LionBot,
                 guildid: int, channelid: int, **kwargs):
        self.settings = bot.get_cog('Economy').settings
        super().__init__(bot, guildid, channelid, **kwargs)

    async def make_message(self) -> MessageArgs:
        t = self.bot.translator.t
        title = t(_p(
            'ui:economy_config|embed|title',
            "Economy Configuration Panel"
        ))
        embed = discord.Embed(
            colour=discord.Colour.orange(),
            title=title
        )
        for setting in self.instances:
            embed.add_field(**setting.embed_field, inline=False)

        args = MessageArgs(embed=embed)
        return args

    async def reload(self):
        lguild = await self.bot.core.lions.fetch_guild(self.guildid)
        self.instances = tuple(
            lguild.config.get(cls.setting_id)
            for cls in self.setting_classes
        )

    async def refresh_components(self):
        await asyncio.gather(
            self.edit_button_refresh(),
            self.close_button_refresh(),
            self.reset_button_refresh(),
        )
        self.set_layout(
            (self.edit_button, self.reset_button, self.close_button),
        )


class EconomyDashboard(DashboardSection):
    section_name = _p(
        'dash:economy|title',
        "Economy Configuration ({commands[config economy]})"
    )
    _option_name = _p(
        "dash:economy|dropdown|placeholder",
        "Economy Panel"
    )
    configui = EconomyConfigUI
    setting_classes = EconomyConfigUI.setting_classes
