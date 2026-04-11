import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from utils.helpers import PATHS
from server.routers import auth, system, config, tasks, agents, users, diary

server = FastAPI(title="CoStaff Dashboard")
server.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
server.mount("/css", StaticFiles(directory=os.path.join(PATHS["frontend"], "css")), name="css")
server.mount("/js", StaticFiles(directory=os.path.join(PATHS["frontend"], "js")), name="js")
server.mount("/views", StaticFiles(directory=os.path.join(PATHS["frontend"], "views")), name="views")

# Include all routers
server.include_router(auth.router)
server.include_router(system.router)
server.include_router(config.router)
server.include_router(tasks.router)
server.include_router(agents.router)
server.include_router(users.router)
server.include_router(diary.router)


@server.get("/")
def read_index():
    return FileResponse(os.path.join(PATHS["frontend"], "index.html"))
