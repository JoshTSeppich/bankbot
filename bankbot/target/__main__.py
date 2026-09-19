"""`python -m bankbot.target`: serve the demo app on 127.0.0.1:8000 for a human to look at."""

import uvicorn

from bankbot.target.app import create_app
from bankbot.target.server import LOOPBACK

HUMAN_PORT = 8000

if __name__ == "__main__":
    uvicorn.run(create_app(), host=LOOPBACK, port=HUMAN_PORT)
