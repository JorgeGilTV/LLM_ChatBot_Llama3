import os
import json
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

def ask_bedrock(prompt: str, selected_tools: list = None, enable_mcp_access: bool = False) -> str:
    """
    Call AWS Bedrock API using Claude Sonnet 4.6 with optional MCP tool access
    
    Args:
        prompt: The user's question/prompt
        selected_tools: List of selected tools (for context)
        enable_mcp_access: If True, Bedrock can access and execute MCP tools
    
    Returns:
        str: The AI response formatted as HTML
    """
    try:
        # Get AWS Bedrock API key from environment
        # BEDROCK_API_KEY is the special Bedrock API key (starts with ABSK)
        bedrock_api_key = os.getenv("BEDROCK_API_KEY")
        
        if not bedrock_api_key:
            return "Error: BEDROCK_API_KEY is not defined in env file."
        
        # If MCP access is enabled, augment the prompt with MCP tools
        if enable_mcp_access:
            try:
                # Import MCP client
                import sys
                sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
                from ask_arlochat import SimpleMCPClient, MCP_SERVER_URL
                
                # Connect to MCP and get available tools
                print("🔗 Bedrock: Connecting to MCP server for tool access...")
                mcp_client = SimpleMCPClient(MCP_SERVER_URL)
                
                if mcp_client.initialize():
                    mcp_tools = mcp_client.list_tools()
                    if mcp_tools:
                        tools_list = "\n".join([f"- {t.get('name')}: {t.get('description', 'N/A')}" for t in mcp_tools[:30]])
                        
                        prompt = f"""You have access to MCP (Model Context Protocol) tools that can query real data.

Available MCP Tools:
{tools_list}

If the user's question requires data lookup, you can specify which MCP tools to use by responding with:
{{"needs_mcp": true, "tools": [{{"name": "tool_name", "params": {{"param": "value"}}}}]}}

User Question: {prompt}

If you need to use MCP tools, respond with the JSON format above.
Otherwise, provide a direct HTML answer."""
                        
                        print(f"✅ Bedrock: Added {len(mcp_tools)} MCP tools to context")
            except Exception as e:
                print(f"⚠️  Could not connect to MCP: {e}")
        
        # Enhance prompt if Ask_Bedrock tool is selected
        elif selected_tools and ("Ask_Bedrock" in selected_tools or "Ask_Gemini" in selected_tools):
            prompt = f"""Execute the following prompt. Return ONLY raw HTML content (no markdown code blocks, no ```html tags).
The HTML should be ready to insert directly into a webpage using innerHTML.

User prompt: {prompt}

IMPORTANT: Return ONLY the HTML content, without wrapping it in markdown code blocks."""
        
        # Set the API key as environment variable for boto3
        # AWS Bedrock API keys (ABSK) are recognized by boto3 via AWS_BEARER_TOKEN_BEDROCK
        os.environ['AWS_BEARER_TOKEN_BEDROCK'] = bedrock_api_key
        
        region = os.getenv("AWS_REGION", "us-east-1")
        
        # Configure timeouts for long-running Bedrock operations
        # When max_tokens=8000, responses can take 2-4 minutes to generate
        config = Config(
            connect_timeout=60,      # 60s to establish connection
            read_timeout=300,        # 5 minutes to read full response (for long generations)
            retries={'max_attempts': 2, 'mode': 'standard'}
        )
        
        # Initialize Bedrock Runtime client
        # boto3 will automatically use AWS_BEARER_TOKEN_BEDROCK for authentication
        bedrock_runtime = boto3.client(
            service_name='bedrock-runtime',
            region_name=region,
            config=config
        )
        
        # Inference Profile ID for Claude Sonnet 4.6 (latest model as of Feb 2026)
        # Using US system-defined inference profile (routes across us-east-1, us-east-2, us-west-2)
        model_id = "us.anthropic.claude-sonnet-4-6"
        
        # Prepare the request body
        request_body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 8000,  # Increased from 4096 to allow longer responses (Claude Sonnet 4 max: 8192)
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.7
        }
        
        # Call Bedrock API
        response = bedrock_runtime.invoke_model(
            modelId=model_id,
            body=json.dumps(request_body)
        )
        
        # Parse response
        response_body = json.loads(response['body'].read())
        
        # Extract the text from the response
        if 'content' in response_body and len(response_body['content']) > 0:
            output = response_body['content'][0].get('text', '')
            
            if not output:
                return "Bedrock is not working properly."
            
            # Post-process: Remove markdown code blocks if present
            # Claude often wraps HTML in ```html ... ``` blocks
            import re
            
            # Pattern to match code blocks: ```language\n...code...\n```
            code_block_pattern = r'^```(?:html|xml)?\s*\n(.*?)\n```$'
            match = re.match(code_block_pattern, output.strip(), re.DOTALL)
            
            if match:
                # Extract content from code block
                output = match.group(1)
            
            return output
        else:
            return f"Unexpected response format: {response_body}"
            
    except ClientError as e:
        error_code = e.response.get('Error', {}).get('Code', 'Unknown')
        error_message = e.response.get('Error', {}).get('Message', str(e))
        return f"AWS Bedrock Error ({error_code}): {error_message}"
    except Exception as e:
        return f"Error executing AWS Bedrock: {e}"


def summarize_page_for_slack(
    page_text: str,
    page_title: str = "",
    source_url: str = "",
) -> str:
    """
    Summarize OneView page text for Slack (English body, Slack mrkdwn-friendly).
    Returns error strings starting with "Error:" or "AWS Bedrock Error" on failure.
    """
    import re

    if not (page_text or "").strip():
        return "Error: empty page text."

    meta_lines = []
    if (page_title or "").strip():
        meta_lines.append(f"Page / UI label (may be non-English): {page_title.strip()}")
    if (source_url or "").strip():
        meta_lines.append(f"URL: {source_url.strip()}")
    meta_block = "\n".join(meta_lines) if meta_lines else "(no extra metadata)"

    prompt = f"""{meta_block}

You are an SRE analyst. Summarize the following text scraped from a OneView GOC AI screen (tool results, Datadog status monitor, status wall, Splunk hints, etc.). The source text may be in Spanish, English, or mixed.

OUTPUT (all in English):
- First line (same line): *Quick read:* then one concise sentence.
- Then structured sections in Slack mrkdwn:
  • Short line *Overall:* followed by one or two sentences.
  • If there are issues: a line *Alerts & status:* then bullet lines starting with "• ".
  • *Notable services / metrics:* with "• " bullets for names, error rates, latency, or counts when present.
  • Optional *Context:* only if it helps (environment, time range, region).
- Use *Label:* at the start of a line for small headings (asterisks for bold in Slack).
- Use "• " for every list item. Add a blank line between sections.
- Do NOT use HTML, code fences, or JSON. Do NOT use # headings.
- Stay under ~30 lines and ~900 words.

--- PAGE TEXT ---
{page_text}
--- END ---"""

    out = ask_bedrock(prompt, selected_tools=None, enable_mcp_access=False)
    if not out or not str(out).strip():
        return "Error: Bedrock returned an empty response."
    s = str(out).strip()
    if s.startswith("Error:") or s.startswith("AWS Bedrock Error") or "Bedrock is not working" in s:
        return s
    s = re.sub(r"<[^>]{1,300}>", "", s)
    s = re.sub(r"\n{4,}", "\n\n\n", s.strip())
    return s
