from babel.translator import LocalBabel
babel = LocalBabel('sysadmin')


async def setup(bot):
    from .exec_cog import Exec
    from .blacklists import Blacklists
    from .guild_log import GuildLog
    from .presence import PresenceCtrl

    from .dash import LeoSettings
    await bot.add_cog(LeoSettings(bot))

    await bot.add_cog(Blacklists(bot))
    await bot.add_cog(Exec(bot))
    await bot.add_cog(GuildLog(bot))
    await bot.add_cog(PresenceCtrl(bot))
