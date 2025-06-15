import asyncio
import json
import uuid
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
import uvicorn
from rabbitmq_client import RabbitMQClient
from websocket_manager import WebSocketManager
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="RabbitMQ WebSocket API")

# Global instances
rabbitmq_client = None
websocket_manager = WebSocketManager()

# Store pending requests for correlation
pending_requests: Dict[str, asyncio.Future] = {}

@app.on_event("startup")
async def startup_event():
    """Initialize RabbitMQ connection and start listening for responses"""
    global rabbitmq_client
    
    try:
        rabbitmq_client = RabbitMQClient()
        await rabbitmq_client.connect()
        
        # Start listening for responses from the processor
        asyncio.create_task(listen_for_responses())
        logger.info("FastAPI service started successfully")
        
    except Exception as e:
        logger.error(f"Failed to start service: {e}")
        raise

@app.on_event("shutdown")
async def shutdown_event():
    """Clean up connections"""
    global rabbitmq_client
    
    if rabbitmq_client:
        await rabbitmq_client.close()
        logger.info("FastAPI service shutdown complete")

async def listen_for_responses():
    """Listen for responses from the processor and route them to appropriate clients"""
    try:
        async for message in rabbitmq_client.consume_responses():
            message_data = json.loads(message.body.decode())
            correlation_id = message_data.get('correlation_id')
            response_text = message_data.get('response', 'No response')
            
            logger.info(f"Received response for correlation_id: {correlation_id}")
            
            # Handle WebSocket responses
            if correlation_id in websocket_manager.active_connections:
                websocket = websocket_manager.active_connections[correlation_id]
                try:
                    await websocket.send_text(response_text)
                    logger.info(f"Sent WebSocket response: {response_text}")
                except Exception as e:
                    logger.error(f"Failed to send WebSocket message: {e}")
            
            # Handle streaming response futures
            if correlation_id in pending_requests:
                future = pending_requests.pop(correlation_id)
                if not future.done():
                    future.set_result(response_text)
                    
    except Exception as e:
        logger.error(f"Error in response listener: {e}")

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for bidirectional communication"""
    connection_id = str(uuid.uuid4())
    await websocket_manager.connect(websocket, connection_id)
    
    try:
        await websocket.send_text(f"Connected with ID: {connection_id}")
        
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            logger.info(f"Received WebSocket message: {data}")
            
            # Generate correlation ID for this request
            correlation_id = str(uuid.uuid4())
            
            # Store the websocket for this correlation ID
            websocket_manager.active_connections[correlation_id] = websocket
            
            # Publish message to RabbitMQ
            message = {
                'message': data,
                'correlation_id': correlation_id,
                'timestamp': asyncio.get_event_loop().time()
            }
            
            await rabbitmq_client.publish_message(json.dumps(message))
            logger.info(f"Published message with correlation_id: {correlation_id}")
            
    except WebSocketDisconnect:
        logger.info(f"WebSocket {connection_id} disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        websocket_manager.disconnect(connection_id)

@app.get("/stream/{message}")
async def stream_endpoint(message: str):
    """HTTP endpoint with streaming response"""
    async def generate_response():
        try:
            # Generate correlation ID
            correlation_id = str(uuid.uuid4())
            
            # Create future for this request
            future = asyncio.Future()
            pending_requests[correlation_id] = future
            
            # Publish message to RabbitMQ
            message_data = {
                'message': message,
                'correlation_id': correlation_id,
                'timestamp': asyncio.get_event_loop().time()
            }
            
            await rabbitmq_client.publish_message(json.dumps(message_data))
            logger.info(f"Published streaming message with correlation_id: {correlation_id}")
            
            # Wait for response with timeout
            try:
                response = await asyncio.wait_for(future, timeout=30.0)
                yield f"data: {response}\n\n"
            except asyncio.TimeoutError:
                yield f"data: Timeout waiting for response\n\n"
            finally:
                # Clean up
                pending_requests.pop(correlation_id, None)
                
        except Exception as e:
            logger.error(f"Streaming error: {e}")
            yield f"data: Error: {str(e)}\n\n"
    
    return StreamingResponse(
        generate_response(),
        media_type="text/plain",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"}
    )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "active_websockets": len(websocket_manager.active_connections),
        "pending_requests": len(pending_requests)
    }

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )