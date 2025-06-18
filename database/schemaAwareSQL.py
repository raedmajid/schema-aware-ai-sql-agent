
import pandas as pd
import re
import os
import sys
import time
import sqlparse
from sqlparse.sql import IdentifierList, Identifier, Function, Token
from sqlparse.tokens import Keyword, DML
from dotenv import load_dotenv
from sqlalchemy import create_engine, inspect
from langchain_openai import ChatOpenAI
from backend.agent.llm_client import LLMClient
from backend.utils.logging_config import logger
from backend.utils.utils import load_config

def initialize():
    """Loads config, connects to DB, and retrieves schema/relationships."""
    global config, RBAC_RULES, RLS_RULES, SENSITIVE_COLUMNS, SQL_INJECTION_PATTERNS
    global engine, full_schema, relationships, llm_client

    logger.info("📌 Initializing system...")

    # Load environment variables
    load_dotenv(".env", override=True)

    # Load Config
    config = load_config()
    RBAC_RULES = config["RBAC_RULES"]
    RLS_RULES = config["RLS_RULES"]
    SQL_INJECTION_PATTERNS = config["SQL_INJECTION_PATTERNS"]
    SENSITIVE_COLUMNS = config["SENSITIVE_COLUMNS"]
    logger.debug(f"👉 Configuration Loaded: {config}")

    # Set up LLM Client
    llm_client = LLMClient(config)

    # Connect to DB
    engine = connect_to_database()

    # Get Schema & Relationships
    full_schema, relationships = get_schema_information(engine)

    logger.info("📌 Initialization complete.")

# Connect to the database
def connect_to_database():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        logger.error("❌ DATABASE_URL is missing! Check .env file.")
        sys.exit(1) 
    try:
        engine = create_engine(database_url)
        return engine
    except Exception as e:
        logger.error(f"❌ Database connection failed: {e}")
        sys.exit(1)

def log_query(user_identity, question, sql_query, execution_status, execution_time=None):
    """Logs query details for audit and compliance."""
    logger.info(f"""
    [QUERY LOG] 
    Timestamp: {pd.Timestamp.now()}
    User: {user_identity["username"]} (Role: {user_identity["role"]}, ID: {user_identity["user_id"]})
    Natural Language Request: "{question}"
    Generated Query: {sql_query}
    Execution Status: {execution_status}
    Execution Time: {execution_time if execution_time else "N/A"} seconds
    """)

def log_security_violation(user_identity, sql_query, reason):
    """Logs unauthorized query attempts for security auditing."""
    logger.warning(f"""
    [SECURITY ALERT] Unauthorized Query Attempt Detected!
    Timestamp: {pd.Timestamp.now()}
    User: {user_identity["username"]} (Role: {user_identity["role"]}, ID: {user_identity["user_id"]})
    Denied SQL Query: {sql_query}
    Reason: {reason}
    """)

def extract_valid_tables_and_columns(sql_query):
    """Extracts valid tables and columns from an SQL query while skipping functions and aliases."""
    parsed = sqlparse.parse(sql_query)
    tables = set()
    columns = set()

    for statement in parsed:
        from_seen = False  # Track when we enter FROM or JOIN clauses

        for token in statement.tokens:
            # ✅ Ignore SQL functions (SUM, COUNT, EXTRACT, etc.)
            if isinstance(token, Function):
                continue  

            # ✅ Detect FROM and JOIN keywords (indicating table references)
            if token.ttype in Keyword and token.value.upper() in {"FROM", "JOIN"}:
                from_seen = True  
                continue  # Move to next token

            # ✅ Identify table names only after FROM / JOIN
            if from_seen and isinstance(token, Identifier):  # **Ensure it's an Identifier**
                real_table = token.get_real_name()
                if real_table and real_table in full_schema:  # Validate against actual schema
                    tables.add(real_table)

            elif from_seen and isinstance(token, IdentifierList):
                for identifier in token.get_identifiers():
                    if isinstance(identifier, Identifier):  # ✅ Ensure it's an Identifier
                        real_table = identifier.get_real_name()
                        if real_table and real_table in full_schema:
                            tables.add(real_table)

            # ✅ Identify SELECT columns and ignore aliases
            if isinstance(token, IdentifierList):
                for identifier in token.get_identifiers():
                    if isinstance(identifier, Identifier):  # ✅ Ensure it's an Identifier
                        parent_table = identifier.get_parent_name()
                        real_name = identifier.get_real_name()

                        if parent_table and real_name:
                            if parent_table in full_schema and real_name in full_schema[parent_table]:
                                columns.add((parent_table, real_name))
                        elif real_name:
                            if any(real_name in full_schema[t] for t in full_schema):
                                columns.add(("", real_name))

            elif isinstance(token, Identifier):  # ✅ Ensure it's an Identifier
                parent_table = token.get_parent_name()
                column_name = token.get_real_name()

                if parent_table and column_name:
                    if parent_table in full_schema and column_name in full_schema[parent_table]:
                        columns.add((parent_table, column_name))
                elif column_name:
                    if any(column_name in full_schema[t] for t in full_schema):
                        columns.add(("", column_name))

    return tables, columns

# Log access to sensitive data
def log_sensitive_data_access(user_identity, sql_query):
    """Logs access only when sensitive columns are accessed."""
    accessed_tables, accessed_columns = extract_valid_tables_and_columns(sql_query)

    accessed_sensitive_data = []

    # Check explicit table.column references
    for table, column in accessed_columns:
        if table in SENSITIVE_COLUMNS and column in SENSITIVE_COLUMNS[table]:
            accessed_sensitive_data.append(f"{table}.{column}")
        elif column in SENSITIVE_COLUMNS.get(table, []):
            accessed_sensitive_data.append(f"{table}.{column}")

    if accessed_sensitive_data:
        logger.debug(f"""
        📌 [DATA ACCESS LOG] 
        Timestamp: {pd.Timestamp.now()}
        User: {user_identity["username"]} (Role: {user_identity["role"]}, ID: {user_identity["user_id"]})
        Accessed SQL Query: {sql_query}
        Sensitive Columns Accessed: {', '.join(accessed_sensitive_data)}
        """)

# Get user identity (role and user_id)
def get_user_identity():
    return {
        "role": os.getenv("USER_ROLE", "customer"),  # Default role
        "user_id": os.getenv("USER_ID"),  # User-specific ID (customer_id or employee_id)
        "username": os.getenv("USERNAME"),  # Optional
    }

# Get the schema information of the database
def get_schema_information(engine):
    inspector = inspect(engine)
    schema_info = {}
    relationships = {}
    for table_name in inspector.get_table_names():
        columns = inspector.get_columns(table_name)
        schema_info[table_name] = [column["name"] for column in columns]

        # Extract foreign key relationships
        foreign_keys = inspector.get_foreign_keys(table_name)
        for fk in foreign_keys:
            parent_table = fk["referred_table"]
            child_table = table_name
            parent_column = fk["referred_columns"][0]
            child_column = fk["constrained_columns"][0]

            relationships[(child_table, parent_table)] = (f"{child_table}.{child_column}", f"{parent_table}.{parent_column}")

    return schema_info, relationships

# Filter schema based on user role (RBAC)
def get_filtered_schema(user_role, full_schema):
    if user_role not in RBAC_RULES:
        return {}  # No access

    filtered_schema = {}
    allowed_tables = RBAC_RULES[user_role]

    for table, columns in full_schema.items():
        if table in allowed_tables:
            filtered_schema[table] = [col for col in columns if col in allowed_tables[table]]
    return filtered_schema
#enforce RLS
def enforce_row_level_security(sql_query, user_identity):
    """Ensure row-level security without duplicating conditions, correctly handling integer user_id."""
    role = user_identity["role"]
    user_id = user_identity["user_id"]

    if role in RLS_RULES:
        condition_template = RLS_RULES[role]

        # Ensure user_id is formatted correctly: quoted only if it's a string
        if isinstance(user_id, int) or user_id.isdigit():
            user_id_value = str(user_id)  # Keep integers without quotes
        else:
            user_id_value = f"'{user_id}'"  # Quote string values

        condition = condition_template.format(user_id=user_id_value)

        # Extract table and column name from condition (e.g., "orders.employee_id = 8")
        match = re.search(r"(\w+)\.(\w+) =", condition)
        if match:
            table_name, column_name = match.groups()
        else:
            return sql_query  # If condition format is unexpected, do nothing

        # Also check for the non-prefixed condition (e.g., "employee_id = 8")
        condition_no_prefix = f"{column_name} = {user_id_value}"

        # Check if the condition (with or without table prefix) is already in the query
        if condition in sql_query or condition_no_prefix in sql_query:
            return sql_query  # Condition already exists, do nothing

        # Append condition safely
        if "WHERE" in sql_query.upper():
            sql_query += f" AND {condition}"
        else:
            sql_query += f" WHERE {condition}"

    logger.info("✅ RLS applied successfully!")
    return sql_query

# SQL Injection Detection Patterns
def detect_sql_injection(sql_query):
    """Detects and logs potential SQL injection patterns."""
    for pattern in SQL_INJECTION_PATTERNS:
        match = re.search(pattern, sql_query, re.IGNORECASE)
        if match:
            logger.warning(f"SQL Injection Detected! Pattern: {pattern}, Match: {match.group(0)}")
            return True
    return False

def query_safe_and_authorized(sql_query, user_identity):
    """Validates query security using real database schema."""
    
    # Ensure query is SELECT-only
    if not sql_query.strip().upper().startswith("SELECT"):
        log_security_violation(user_identity, sql_query, "Forbidden Query Type")
        return {"error": "Security Violation", "message": "🚨 Forbidden query type. Only SELECT queries are allowed."}
    
        # Check for SQL Injection Patterns
    if detect_sql_injection(sql_query):
        log_security_violation(user_identity, sql_query, "Potential SQL Injection Detected")
        return {"error": "Security Violation", "message": "🚨 Potential SQL injection detected."}

    # ✅ Extract tables & columns correctly
    tables, columns = extract_valid_tables_and_columns(sql_query)

    # ✅ Validate tables against allowed access
    role = user_identity["role"]
    allowed_tables = RBAC_RULES.get(role, {})

    for table in tables:
        if table not in allowed_tables:
            log_security_violation(user_identity, sql_query, f"Unauthorized Table Access: {table}")
            return {"error": "Security Violation", "message": f"🚨 Unauthorized access to table: {table}"}

    # ✅ Validate columns against allowed access
    for table, column in columns:
        if table and table in allowed_tables:
            if column not in allowed_tables[table]:
                log_security_violation(user_identity, sql_query, f"Unauthorized Column Access: {table}.{column}")
                return {"error": "Security Violation", "message": f"🚨 Unauthorized access to column: {table}.{column}"}
        elif not table:
            # Standalone columns (should exist in any allowed table)
            if not any(column in allowed_tables[t] for t in allowed_tables):
                log_security_violation(user_identity, sql_query, f"Unauthorized Column Access: {column}")
                return {"error": "Security Violation", "message": f"🚨 Unauthorized access to column: {column}"}

    logger.info("✅ Query is safe and authorized!")
    return {"status": "SAFE"}  # ✅ Standardized success response

# Generate an SQL query with row-level security
def generate_sql_query(question, schema_info, relationships, user_identity, llm_client):
    role = user_identity["role"]
    user_id = user_identity["user_id"]

    # Build schema description for LLM
    schema_prompt = f"Database Schema for {role} role:\n"
    for table, columns in schema_info.items():
        schema_prompt += f"Table '{table}': {', '.join(columns)}\n"

    # Build relationships description
    relationship_prompt = "\nForeign Key Relationships:\n"
    for (child, parent), (child_col, parent_col) in relationships.items():
        relationship_prompt += f"- `{child_col}` → `{parent_col}`\n"

    # Apply row-level security rules
    row_level_filter = RLS_RULES.get(role, "").format(user_id=user_id)
    
    sql_prompt = f"""
    - you are working with a postgres database
    - You will be provided with a user query.
    - Your goal is to generate a valid SQL query to provide the best answer to the user with the '{role}' role.
    - The user is identified by `user_id = {user_id}`.
    - Automatically apply the following WHERE condition for security: {row_level_filter} (if applicable).
    - Users can only access authorized tables and columns.
    - If a request requires unauthorized access, return "Access Denied."
    - DO NOT include column names that are NOT in the schema.
    - Always use the provided foreign key mappings when joining tables. DO NOT create new JOIN conditions.
    - ONLY GENERATE "SELECT" queries.
    - NEVR ATTEMPT to modify the data or the database (INSERT, UPDATE, DELETE, Drop, etc.).
    - Never include employee photos in any results
    - DO NOT use table aliases, always reference tables by their full names
    - If the question is not related to the database, return "I don't know."
    - You can order the results by a relevant column to return the most interesting examples in the database
    - always list field names in the SELECT clause, dont use "SELECT *"
    - YOUR RESPONSE MUST ONLY INCLUDE THE SQL QUERY> NO ADDITIONAL TEXT
    - This is the database schema  you should use:
    - {schema_prompt}
    - This are the relationsships you should use:
    - {relationship_prompt}
    - If the user provides a clear request, generate the SQL query.
    - If the request is vague or ambiguous, **DO NOT GUESS**. Instead, respond with `CLARIFY:` followed by a short clarifying question.
    - [Example 1:]
    - User: "Show orders"
    - AI: "CLARIFY: Do you want all orders, or just active ones? Should I include total order amount?"
    - [Example 2:]
    - User: "Get sales data"
    - AI: "CLARIFY: Sales data by product, region, or customer? Do you need total revenue or individual transactions?"
    - Now, answer the following request:
        User: "{question}"
        AI:
    """.strip()

    logger.debug(f"👉 SQL Prompt Sent to LLM:\n{sql_prompt}")

    response_text = llm_client.llm_client_generate_sql_query(sql_prompt)

    if isinstance(response_text, dict):
        logger.error(f"📌 Response from LLM: {response_text}")
        #return {"error": "Unexpected LLM Response", "message": response_text}
        return response_text

    # If the LLM returns an error message, return it as a structured response
    if any(keyword in response_text.lower() for keyword in ["access denied", "i don't know", "not authorized"]):
        logger.warning(f"❌ LLM response indicates restricted access: {response_text}")
        return {"error": "Access Denied or Unauthorized Query", "message": response_text}

    # Return SQL query directly
    return response_text.strip()  # Ensuring it's a clean string

def execute_sql_query(engine, sql_query, user_identity):
    """Executes SQL query after enforcing row-level security and logging sensitive data access."""

    # Step 1: Validate Query Safety & Authorization (RBAC)
    logger.info("📌 Validating generated SQL query for security and RBAC compliance.")
    validation_result = query_safe_and_authorized(sql_query, user_identity)

    if "error" in validation_result:
        logger.error(f"❌ SQL query failed security validation: {validation_result['message']}")
        return validation_result  # ✅ Return structured security violation response

    logger.info("✅ SQL query passed validation and is authorized for execution.")

    # Step 2: Apply Row-Level Security (RLS)
    logger.info("📌 Applying Row-Level Security (RLS)...")
    secured_sql_query = enforce_row_level_security(sql_query, user_identity)
    logger.info("📌 Row-Level Security Applied...")

    # Step 3: Log Access to Sensitive Data
    logger.info("📌 Checking for sensitive data access...")

    try:
        log_sensitive_data_access(user_identity, secured_sql_query)
    except Exception as e:
        logger.error(f"❌ Error in log_sensitive_data_access: {e}")
    logger.info("📌 Access to sensitive data logged (if any)...")

    try:
        # Step 4: Execute Query
        logger.info("📌 Executing SQL query...")
        start_time = time.time()
        with engine.connect() as connection:
            result = pd.read_sql_query(secured_sql_query, connection)
        execution_time = round(time.time() - start_time, 3)
        if result.empty:
            logger.info(f"📌 Query executed successfully in {execution_time} seconds. but no records were found.")
            return {"result": []}    # Return an empty DataFrame instead of `None` 
        else:
            logger.info(f"📌 Query executed successfully in {execution_time} seconds. Retrieved {len(result)} records.")

        return result

    except Exception as e:
        # Step 5: Log execution failure
        logger.error(f"❌ SQL Execution Error: {e}")
        log_query(user_identity, "Query Execution Failed", secured_sql_query, f"Error: {e}")
        raise

def process_query(question, user_identity):
    """
     Main function to process user input:
    - Filters schema based on RBAC
    - Generates SQL using LLM
    - Executes SQL
    - Returns results
    """
    logger.debug(f"📌 Processing question: {question} for user: {user_identity}")

     # Apply Role-Based Access Control (RBAC)
    schema_info = get_filtered_schema(user_identity["role"], full_schema)

    # Generate SQL Query using LLM (this should return an SQL query or error message)
    sql_query = generate_sql_query(question, schema_info, relationships, user_identity, llm_client)

    # **If `generate_sql_query()` already returns an error, stop execution**
    if isinstance(sql_query, dict) and "error" in sql_query:
        logger.warning(f"❌ Query generation failed: {sql_query['message']}")
        return sql_query  # Return error message without executing

    logger.info(f"📌 Generated SQL Query:\n{sql_query}")

    # Validate the SQL query (Optional)
    try:
        query_quality = llm_client.llm_client_validate_sql(sql_query) # Placeholder for validation model
        logger.debug(f"🔍 Query Quality: {query_quality}")
    except Exception as e:
        logger.error(f"❌ Query validation failed: {e}")
        return {"error": "Query Validation Failed", "message": str(e)}

    # 🔍 **Try to execute the query, catching any execution errors**
    try:
        result = execute_sql_query(engine, sql_query, user_identity)

        if isinstance(result, dict) and "error" in result:
            logger.warning(f"❌ Query execution blocked: {result['message']}")
            return result  # ✅ Return security or authorization failure properly

        return {
            "user": user_identity,
            "question": question,
            "generated_sql": sql_query,
            "query_quality": query_quality,
            "records retrieved": len(result),
            "result": result.to_dict(orient="records") if isinstance(result, pd.DataFrame) and not result.empty else [],
        }
    except Exception as e:
        logger.error(f"❌ Query execution failed: {e}")
        return {"error": "Query Execution Failed", "message": str(e)}

    