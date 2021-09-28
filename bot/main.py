from meta import client, conf, log

from data import tables

import core # noqa

import modules  # noqa

# Load and attach app specific data
client.appdata = core.data.meta.fetch_or_create(conf.bot['data_appid'])
client.data = tables

# Initialise all modules
client.initialise_modules()

# Log readyness and execute
log("Initial setup complete, logging in", context='SETUP')
client.run(conf.bot['TOKEN'])
