from typing import Dict
from fastapi import WebSocket
import logging

logger = logging.getLogger(__name__)

class WebSocketManager:
    def __init__(self):
        self.connections: Dict[str, WebSocket] = {} # Store WebSocket connections by connection ID
        self.active_connections: Dict[str, WebSocket] = {} # Store WebSocket connections by correlation ID for message routing
    
    async def connect(self, websocket: WebSocket, connection_id: str):
        """Accept WebSocket connection and store it"""
        await websocket.accept()
        self.connections[connection_id] = websocket
        logger.info(f"WebSocket connected: {connection_id}")
    
    def disconnect(self, connection_id: str):
        """Remove WebSocket connection and clean up correlation IDs"""
        websocket = self.connections.get(connection_id)

        # Step 1: Remove the connection
        if websocket:
            del self.connections[connection_id]
            logger.info(f"WebSocket disconnected: {connection_id}")

        # Step 2: Remove all correlation_ids that point to this websocket
        to_remove = [cid for cid, ws in self.active_connections.items() if ws == websocket]

        for cid in to_remove:
            del self.active_connections[cid]
    
    async def send_personal_message(self, message: str, websocket: WebSocket):
        """Send message to specific WebSocket"""
        try:
            await websocket.send_text(message)
        except Exception as e:
            logger.error(f"Failed to send message to WebSocket: {e}")
    
    async def broadcast(self, message: str):
        """Broadcast message to all connected WebSockets"""
        disconnected = []
        for connection_id, websocket in self.connections.items():
            try:
                await websocket.send_text(message)
            except Exception as e:
                logger.error(f"Failed to broadcast to {connection_id}: {e}")
                disconnected.append(connection_id)
        
        # Clean up disconnected WebSockets
        for connection_id in disconnected:
            self.disconnect(connection_id)