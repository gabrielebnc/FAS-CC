from fastapi import FastAPI
from .routers import adaptation_options, adaptation_options_schema, execute_schema, monitor_schema, monitor, execute
from .connectors import KafkaConsumerMonitor
from contextlib import asynccontextmanager
import asyncio

@asynccontextmanager
async def startup(app: FastAPI):
    await asyncio.sleep(5)
    asyncio.create_task(KafkaConsumerMonitor.connect())
    yield

app = FastAPI(lifespan=startup)
    
@app.get("/")
async def root():
    return {"message": "CrowdNav-FAS2024"}

app.include_router(monitor.router)
app.include_router(monitor_schema.router)
app.include_router(execute.router)
app.include_router(execute_schema.router)
app.include_router(adaptation_options.router)
app.include_router(adaptation_options_schema.router)
