from fastapi import FastAPI, HTTPException, Depends
from pydantic import BaseModel, Field
from database.schemaAwareSQL import initialize, process_query
from backend.utils.utils import get_user_identity # authentication logic. replace with your own
from backend.agent.ai_agent import AIAgent
from backend.utils.logging_config import logger

# Initialize system before serving API
initialize()

app = FastAPI()

ai_agent = AIAgent()

# Pydantic request models
class QueryRequest(BaseModel):
    question: str = Field(..., example="What are the top 5 customers by revenue")

class ChatRequest(BaseModel):
    query: str

#change this method to use your IAM or policy engine to authenticate users
def authenticate_user():
    """Dependency to authenticate user based on environment variables."""
    user_identity = get_user_identity()
    if not user_identity["user_id"]:
        raise HTTPException(status_code=401, detail="Unauthorized access: User ID is missing")
    logger.info(f"📌 User authenticated: {user_identity}")
    return user_identity

# Root endpoint
@app.get("/", summary="Root API Endpoint", response_model=dict)
def root():
    """Welcome message for the API root endpoint."""
    return {"message": "Welcome to the AI-Powered SQL API! Use /docs for API documentation."}

# `/ask` - use API endpoint to directly execute SQL queries
@app.post("/ask", summary="Process AI-powered query through API endpoint", response_model=dict)
def ask(request: QueryRequest, user_identity: dict = Depends(authenticate_user)):
    """
    Takes a natural language query, generates SQL, executes it, and returns results.
    """
    logger.info("📌 Received client request over API endpoint: /ask")

    response = process_query(request.question, user_identity)

    logger.debug(f"📌 API Response Before Sending: {response}")  # ✅ Log what API is actually sending 

    return handle_response(response)  # Consistent  handling

# `/chat` - Use AI agent to answer questions and refine SQL queries
@app.post("/chat", summary="Process AI-powered query through AI agent", response_model=dict)
def chat(request: ChatRequest, user_identity: dict = Depends(authenticate_user)):
    """
    Conversational AI chat that keeps context and refines SQL queries.
    """
    logger.info("📌 Received client request over AI Agent endpoint: /chat")

    response = ai_agent.chat(request.query, user_identity) 

    logger.debug(f"📌 API Response Before Sending: {response}")  # ✅ Log what API is actually sending

    return handle_response(response)  # Consistent  handling

def handle_response(response):

    logger.debug(f"📌 API Response Received: {response}")  # ✅ Log the actual response received
    if isinstance(response, dict) and "error" in response:
        error_message = response.get("message", "⚠️ An unexpected error occurred.")
        error_type = response.get("error", "")

        # Extract Nested Error Messages**
        if isinstance(error_message, dict) and "error" in error_message:
            logger.warning(f"🔄 Extracting nested error: {error_message}")
            return error_message  # ✅ Return the actual inner error message instead of wrapping

        if error_type.lower() == "unsupported question":
            logger.warning(f"🚫 Unsupported Question: {error_message}")
            return response
        
        if error_type.lower() == "access denied":
            logger.warning(f"🚫 Access Denied: {error_message}") 
            return {
                "error": "Access Denied",
                "message": "🚫 LLM has determined that you do not have permission to access this data."
            }
        
        if error_type.lower() == "clarification needed":
            logger.warning(f"🤔 Clarification Needed: {error_message}") 
            return {
                "error": "Clarification Needed",
                "message": f"🤔 Clarification Needed: {error_message}"
            }

        if "access denied" in str(error_message).lower():
            logger.warning(f"🚫 Access Denied: {error_message}") 
            return {
                "error": "Access Denied",
                "message": "🚫 LLM has determined that you do not have permission to access this data."
            }
        
        elif "sql query failed security validation" in error_message.lower():  # 🔥 **New Check**
            logger.error(f"🛑 SQL Security Blocked: {error_message}") 
            return {
                "error": "Security Violation",
                "message": "🚨 Your query was blocked due to security restrictions."
            }

        elif "i don't know" in str(error_message).lower() or "not authorized" in str(error_message).lower() or "I can only answer questions about the database" in str(error_message).lower():
            logger.warning(f"unknown topic: {error_message}") 
            return {
                "error": "Unknown Query",
                "message": "I will only answer questions about the database I'm connected to."
            }

        else:
            logger.error(f"🚫 Unexpected API Error: {error_message}")  
            return {
                "error": "Unexpected Error",
                "message": str(error_message)  # ✅ Convert to string only if needed
            }

    return response  # ✅ Return the correct response structure

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000, reload=True)