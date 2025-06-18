import re # For regex
import os # For environment variables
import requests # For OpenRouter API requests
import ollama  # For Ollama-managed models
from dotenv import load_dotenv # For loading environment variables
from langchain_openai import ChatOpenAI # For OpenAI models
from llama_cpp import Llama  # For local Llama models
from transformers import pipeline # For Hugging Face models
from backend.utils.logging_config import logger # For logging

# Load environment variables
load_dotenv()  

class LLMClient:
    def __init__(self, config):
        """Initialize the LLM client based on the selected provider."""
        self.provider = config.get("LLM_PROVIDER", "").strip().lower()  # Normalize value
        logger.info(f"📌 Initializing LLMClient with provider: {self.provider}")

        try:
            if self.provider == "openai":
                self.model = config["OPENAI"].get("MODEL", "gpt-4")
                self.api_key = os.getenv("OPENAI_API_KEY")

                unsupported_temperature_models = ["o3-mini-2025-01-31"]

                openai_kwargs = {
                    "model": self.model,
                    "openai_api_key": self.api_key
                }
                if self.model not in unsupported_temperature_models:
                    openai_kwargs["temperature"] = 0  # Only add `temperature` if supported

                self.client = ChatOpenAI(**openai_kwargs)

                logger.info(f"📌 Using OpenAI model: {self.model}")

            elif self.provider == "openrouter":
                self.model = config["OPENROUTER"]["MODEL"]
                self.api_key = os.getenv("OPENROUTER_API_KEY")
                self.base_url = config["OPENROUTER"]["BASE_URL"]
                logger.info(f"📌 Using OpenRouter model: {self.model}, API URL: {self.base_url}")

            elif self.provider == "llama":
                self.model = config["LLAMA"]["HF_MODEL"]
                self.api_key = config["LLAMA"]["API_KEY"]
                logger.info(f"📌 Using Llama HF model: {self.model}")

            elif self.provider == "llama_local":
                self.model_path = config["LLAMA_LOCAL"]["MODEL_PATH"]
                if not self.model_path:
                    logger.error("❌ LLAMA_LOCAL MODEL_PATH is not set in config.yaml.")
                    raise ValueError("LLAMA_LOCAL MODEL_PATH is not set in config.yaml.")
                self.client = Llama(model_path=self.model_path)
                logger.info(f"📌 Using local Llama model at: {self.model_path}")

            elif self.provider == "ollama":
                self.model = config["OLLAMA"]["MODEL"]
                self.client = ollama  # Ollama manages local Llama models
                logger.info(f"📌 Using Ollama model: {self.model}")

            else:
                logger.error(f"❌ Unsupported LLM Provider: {self.provider}")
                raise ValueError(f"Unsupported LLM Provider: {self.provider}")
        
        except KeyError as e:
            logger.error(f"❌ Missing configuration key: {e}")
            raise

    def _query_openrouter(self, prompt):
        """Send API request to OpenRouter."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0  # Enforce deterministic responses
        }
        try:
            response = requests.post(f"{self.base_url}/chat/completions", headers=headers, json=data)
            response.raise_for_status()  # Raise error for bad status codes
            logger.debug("👉 OpenRouter API response received successfully.")
            return response.json()["choices"][0]["message"]["content"]
        except requests.exceptions.RequestException as e:
            logger.error(f"❌ OpenRouter API Error: {e}")
            raise
        
    def llm_client_validate_sql(self, sql_query):
        logger.info("📌 Validating SQL query using LLM.")
        prompt = f"Analyze the following SQL query for logical errors, inefficiencies, and security risks. please update the query for any critical findings:\n\n{sql_query}"
        response = "PLACEHOLDER: call your validation model here. it can be the same model or a different one" # Placeholder for validation model
        return response  # AI-generated validation feedback
        
    def llm_client_generate_sql_query(self, prompt):
        """Generates SQL query using the selected LLM provider."""
        logger.info("📌 Generating SQL query...")

        try:

            if self.provider == "openai":
                response_text = self.client.invoke(prompt).content
                response = self.extract_sql(response_text)

            elif self.provider == "llama":
                generator = pipeline("text-generation", model=self.model)
                response_text = generator(prompt, max_length=256)[0]["generated_text"]
                response = self.extract_sql(response_text)

            elif self.provider == "llama_local":
                response_text = self.client(prompt, temperature=0)["choices"][0]["text"]
                response = self.extract_sql(response_text)

            elif self.provider == "ollama":
                response_text = self.client.chat(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    options={
                        "temperature": 0,   # Enforce deterministic responses
                        "num_threads": 16,   # Increase CPU usage
                        "num_ctx": 4096,    # Reduce context size if RAM is low
                        "num_predict": 1024, # Limit output size for faster execution
                        "f16_kv": True,     # Enable floating-point optimization
                    },
                )
                response = self.extract_sql(response_text.message.content)

            elif self.provider == "openrouter":
                response_text = self._query_openrouter(prompt)
                response = self.extract_sql (response_text)  # Extract SQL from OpenRouter's response

            else:
                raise ValueError("Unsupported LLM Provider")
            
            logger.info("📌 End of generating SQL query")
            logger.debug(f"👉 Original SQL query generated by LLM:{response}")
            return response

        except Exception as e:
            logger.error(f"❌ Error generating SQL query: {e}")
            raise
        
    @staticmethod
    def extract_sql(response_text):  # Remove `self`
        """Extracts the SQL query from LLM response, removing extra text."""

        logger.debug(f"👉 Extracting SQL from response: {response_text[:200]}...")  # Log first 200 chars

        # ✅ **Step 1: Remove AI reasoning (`<think>...</think>`)**
        response_text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL).strip()

        # ✅ **Step 2: Extract SQL if enclosed in a code block (` ```sql ... ``` `)**
        match = re.search(r"```sql(.*?)```", response_text, re.DOTALL)  # Extract SQL inside ```sql ```

        if match:
            sql = match.group(1).strip()
            logger.info("📌 SQL extracted from formatted response.")
            return sql
        
        # ✅ **Step 3: If no code block, find the first valid SELECT statement**
        match = re.search(r"\bSELECT\b\s+.*", response_text, re.IGNORECASE | re.DOTALL)
        if match:
            sql = match.group(0).strip()
            logger.info(f"📌 Direct SQL response detected:\n{sql}")
            return sql

        # Handle known error messages
        lower_text = response_text.lower()
        if "access denied" in lower_text:
            logger.warning("❌ LLM response indicates access is denied.")
            return {"error": "Access Denied", "message": "🚫 LLM has determined that you do not have permission to perform this operation."}

        if "i don't know" in lower_text:
            logger.warning("❌ LLM response indicates it will not answer questions outside the topic of connected database.")
            return {"error": "Unsupported Question", "message": "I will only answer questions about the database I'm connected to"}

        if "not authorized" in lower_text:
            logger.warning("❌ LLM response indicates authorization issue.")
            return {"error": "Authorization Failed", "message": "🚫 Your role does not allow this action."}
        
        if "clarify:" in lower_text:
            logger.warning("❌ LLM response requests clarification from the user.")
            clarification = response_text.replace("CLARIFY:", "").strip()
            return {"error": "Clarification Needed", "message": clarification}
        
        # Handle vague queries where LLM asks for clarification**
        if any(keyword in lower_text for keyword in ["clarify:", "please provide more details", "could you clarify", "what do you mean", "can you specify","your request is not clear"]):
            logger.warning("❌ LLM response requests clarification from the user.")
            return {"error": "Clarification Needed", "message": "🤔 I need more details to generate a useful query. Can you specify?"}


        # **General Failure Case**
        logger.error("❌ No valid SQL query found in response.")
        return {"error": "Invalid SQL Response", "message": response_text}  # Return structured error
    
