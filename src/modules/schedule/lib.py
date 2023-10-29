import asyncio
import itertools
import datetime as dt
from typing import Optional

import discord

from meta.logger import log_wrap
from utils.ratelimits import Bucket
from . import logger, babel

_p, _np = babel._p, babel._np


def time_to_slotid(time: dt.datetime) -> int:
    """
    Return the slotid for the provided time.
    """
    utctime = time.astimezone(dt.timezone.utc)
    hour = utctime.replace(minute=0, second=0, microsecond=0)
    return int(hour.timestamp())


def slotid_to_utc(sessionid: int) -> dt.datetime:
    """
    Convert the given slotid (hour EPOCH) into a utc datetime.
    """
    return dt.datetime.fromtimestamp(sessionid, tz=dt.timezone.utc)


async def batchrun_per_second(awaitables, batchsize):
    """
    Run provided awaitables concurrently,
    ensuring that no more than `batchsize` are running at once,
    and that no more than `batchsize` are spawned per second.

    Returns list of returned results or exceptions.
    """
    bucket = Bucket(batchsize, 1)
    sem = asyncio.Semaphore(batchsize)

    tasks = []
    for awaitable in awaitables:
        await asyncio.gather(bucket.wait(), sem.acquire())
        bucket.request()
        task = asyncio.create_task(awaitable)
        task.add_done_callback(lambda fut: sem.release())
    return await asyncio.gather(*tasks, return_exceptions=True)


async def limit_concurrency(aws, limit):
    """
    Run provided awaitables concurrently,
    ensuring that no more than `limit` are running at once.
    """
    aws = iter(aws)
    aws_ended = False
    pending = set()
    count = 0
    logger.debug("Starting limited concurrency executor")

    while pending or not aws_ended:
        while len(pending) < limit and not aws_ended:
            aw = next(aws, None)
            if aw is None:
                aws_ended = True
            else:
                pending.add(asyncio.create_task(aw))
                count += 1

        if not pending:
            break

        done, pending = await asyncio.wait(
            pending, return_when=asyncio.FIRST_COMPLETED
        )
        while done:
            yield done.pop()
    logger.debug(f"Completed {count} tasks")


def format_until(t, distance):
    if distance:
        return t(_np(
            'ui:schedule|format_until|positive',
            "in <1 hour",
            "in {number} hours",
            distance
        )).format(number=distance)
    else:
        return t(_p(
            'ui:schedule|format_until|now',
            "right now!"
        ))


@log_wrap(action='Vacuum Channel')
async def vacuum_channel(channel: discord.VoiceChannel, reason: Optional[str] = None):
    """
    Launch disconnect tasks for each voice channel member who does not have permission to connect.
    """
    me = channel.guild.me
    if not channel.permissions_for(me).move_members:
        # Nothing we can do
        return

    to_remove = [member for member in channel.members if not channel.permissions_for(member).connect]
    for member in to_remove:
        # Disconnect member from voice
        # Extra check here since members may come and go while we are trying to remove
        if member in channel.members:
            try:
                await member.edit(voice_channel=None, reason=reason)
            except discord.HTTPException:
                pass
