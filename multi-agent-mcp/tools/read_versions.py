import requests, html, os, json
from dotenv import load_dotenv

load_dotenv()

def read_versions(query: str) -> str:
    print("🔎 Reading Arlo Versions:", query)
    """
    Reads version data from https://versions.arlocloud.com/output_version.json
    Requires authentication - uses ATLASSIAN_EMAIL and CONFLUENCE_TOKEN or ARLO_USER/ARLO_PASSWORD from .env
    """
    base_url = "https://versions.arlocloud.com"
    json_url = f"{base_url}/output_version.json"
    
    # Try different authentication methods
    # Method 1: Atlassian credentials (email + token)
    email = os.getenv("ATLASSIAN_EMAIL")
    token = os.getenv("CONFLUENCE_TOKEN")
    
    # Method 2: Arlo-specific credentials (if different)
    arlo_user = os.getenv("ARLO_USER")
    arlo_password = os.getenv("ARLO_PASSWORD")
    
    # Determine which authentication to use
    if arlo_user and arlo_password:
        auth = (arlo_user, arlo_password)
        print(f"Using ARLO credentials: {arlo_user}")
    elif email and token:
        auth = (email, token)
        print(f"Using Atlassian credentials: {email}")
    else:
        return "<p>Error: No credentials found. Please set ARLO_USER/ARLO_PASSWORD or ATLASSIAN_EMAIL/CONFLUENCE_TOKEN in .env file</p>"
    
    # Fetch JSON data with retry logic
    max_retries = 3
    retry_delay = 2
    last_error = None
    
    for attempt in range(max_retries):
        try:
            print(f"🔄 Attempt {attempt + 1}/{max_retries} to fetch versions.arlocloud.com")
            resp = requests.get(json_url, auth=auth, timeout=20)
            break  # Success, exit retry loop
        except requests.exceptions.ConnectionError as e:
            last_error = e
            error_str = str(e)
            if "Name or service not known" in error_str or "Failed to resolve" in error_str:
                # DNS resolution failure - likely not connected to VPN
                return f"""
                <div style='background-color: #fff3cd; padding: 16px; border-left: 4px solid #f59e0b; border-radius: 6px; margin: 12px 0;'>
                    <h3 style='margin: 0 0 8px 0; color: #92400e; font-size: 16px;'>⚠️ Cannot Connect to Arlo Versions</h3>
                    <p style='margin: 0 0 8px 0; color: #78350f; font-size: 13px;'>
                        <strong>Error:</strong> Unable to resolve <code>versions.arlocloud.com</code>
                    </p>
                    <div style='background: #fef3c7; padding: 12px; border-radius: 4px; margin: 8px 0;'>
                        <p style='margin: 0 0 6px 0; color: #92400e; font-weight: bold; font-size: 13px;'>
                            💡 Possible Solutions:
                        </p>
                        <ul style='margin: 0; padding-left: 20px; color: #78350f; font-size: 12px;'>
                            <li><strong>Check VPN:</strong> versions.arlocloud.com is an internal resource - ensure you're connected to Arlo VPN (GlobalProtect)</li>
                            <li><strong>Verify DNS:</strong> Check if you can ping <code>versions.arlocloud.com</code></li>
                            <li><strong>Network issues:</strong> Temporary DNS or connectivity problems</li>
                            <li><strong>Firewall:</strong> Corporate firewall may be blocking access</li>
                        </ul>
                    </div>
                    <div style='background: #e0f2fe; padding: 10px; border-radius: 4px; margin: 8px 0;'>
                        <p style='margin: 0; color: #0c4a6e; font-size: 12px;'>
                            <strong>🔍 Quick Check:</strong><br>
                            • VPN Status: Open GlobalProtect and verify connection<br>
                            • Public IP: Click "🌐 Check IP" button in footer to verify your IP<br>
                            • If IP shows Querétaro (189.x or 187.x), VPN may not be routing this domain
                        </p>
                    </div>
                </div>
                """
            else:
                # Other connection error
                if attempt < max_retries - 1:
                    print(f"⚠️  Connection error, retrying in {retry_delay}s...")
                    import time
                    time.sleep(retry_delay)
                    continue
        except requests.exceptions.Timeout as e:
            last_error = e
            if attempt < max_retries - 1:
                print(f"⚠️  Timeout, retrying in {retry_delay}s...")
                import time
                time.sleep(retry_delay)
                continue
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                print(f"⚠️  Error: {str(e)}, retrying in {retry_delay}s...")
                import time
                time.sleep(retry_delay)
                continue
    else:
        # All retries failed
        return f"""
        <div style='background-color: #fee2e2; padding: 16px; border-left: 4px solid #ef4444; border-radius: 6px; margin: 12px 0;'>
            <h3 style='margin: 0 0 8px 0; color: #991b1b; font-size: 16px;'>❌ Failed to Fetch Arlo Versions</h3>
            <p style='margin: 0 0 8px 0; color: #7f1d1d; font-size: 13px;'>
                Tried {max_retries} times but could not connect to versions.arlocloud.com
            </p>
            <p style='margin: 0; color: #991b1b; font-size: 12px;'>
                <strong>Error:</strong> {html.escape(str(last_error))}
            </p>
        </div>
        """
    
    if resp.status_code == 401:
        return f"<p>Error 401: Authentication failed. Please verify your credentials in the .env file</p>"
    elif resp.status_code != 200:
        return f"<p>Error {resp.status_code}: {html.escape(resp.reason)}</p>"
    
    # Parse JSON data
    try:
        version_data = resp.json()
    except json.JSONDecodeError as e:
        return f"<p>Error parsing JSON: {html.escape(str(e))}</p>"
    
    # Prepare search filter
    search_query = query.strip().lower() if query else ""
    
    # Build HTML output with styling
    output = f"""
    <style>
      .versions-table {{
        border-collapse: collapse;
        width: 100%;
        font-family: 'Courier New', monospace;
        font-size: 12px;
        margin-top: 10px;
        margin-bottom: 20px;
      }}
      .versions-table th {{
        border: 1px solid #444;
        padding: 10px;
        text-align: left;
        background-color: #2c3e50;
        color: white;
        font-weight: bold;
      }}
      .versions-table td {{
        border: 1px solid #ddd;
        padding: 8px;
        text-align: left;
        vertical-align: top;
      }}
      .versions-table tr:nth-child(even) {{
        background-color: #f9f9f9;
      }}
      .versions-table tr:hover {{
        background-color: #e8f4f8;
      }}
      .service-name {{
        font-weight: bold;
        color: #2c3e50;
      }}
      .mismatch {{
        background-color: #fff3cd;
        color: #856404;
        font-weight: bold;
        padding: 2px 6px;
        border-radius: 3px;
      }}
      .no-version {{
        background-color: #f8d7da;
        color: #721c24;
        font-weight: bold;
        padding: 2px 6px;
        border-radius: 3px;
      }}
      .version-ok {{
        color: #155724;
      }}
      .versions-header {{
        color: #2c3e50;
        margin-top: 20px;
        margin-bottom: 10px;
        border-bottom: 2px solid #3498db;
        padding-bottom: 5px;
      }}
      .highlight {{
        background-color: #ffeb3b;
        font-weight: bold;
      }}
      .search-info {{
        padding: 10px;
        background-color: #e3f2fd;
        border-left: 4px solid #2196f3;
        border-radius: 3px;
        margin-bottom: 15px;
      }}
    </style>
    <div class="response-area">
      <h2>📦 Arlo Version Dashboard</h2>
      <p>Source: <a href="{base_url}" target="_blank">{base_url}</a></p>
    """
    
    # Add search info if there's a filter
    if search_query:
        output += f"""
        <div class='search-info'>
            <strong>🔍 Filter applied:</strong> Showing results for "<span class='highlight'>{html.escape(query)}</span>"
        </div>
        """
    
    # Track total results
    total_services_found = 0
    total_groups_shown = 0
    
    # Process each service group in the JSON
    for service_group, services_dict in version_data.items():
        # Filter services based on search query
        filtered_services = {}
        
        if search_query:
            # Check if search matches group name
            group_matches = search_query in service_group.lower()
            
            # Filter services by name or version content
            for service_name, service_data in services_dict.items():
                # Check if service name matches
                if search_query in service_name.lower():
                    filtered_services[service_name] = service_data
                    continue
                
                # Check if search query matches any version value
                for env, version in service_data.items():
                    if not env.endswith('_monitoring'):
                        version_str = str(version).lower()
                        if search_query in version_str:
                            filtered_services[service_name] = service_data
                            break
            
            # If group matches but no services matched, show all services in this group
            if group_matches and not filtered_services:
                filtered_services = services_dict
        else:
            # No filter, show all
            filtered_services = services_dict
        
        # Skip this group if no services match
        if not filtered_services:
            continue
        
        total_groups_shown += 1
        total_services_found += len(filtered_services)
        
        output += f"<h3 class='versions-header'>{html.escape(service_group)}</h3>"
        
        # Count issues for summary
        mismatch_count = 0
        no_version_count = 0
        
        # Get all unique environments from the filtered data
        all_envs = set()
        for service_name, service_data in filtered_services.items():
            for env in service_data.keys():
                if not env.endswith('_monitoring'):
                    all_envs.add(env)
        
        # Sort environments for consistent display
        sorted_envs = sorted(all_envs)
        
        # Create table
        output += "<table class='versions-table'>"
        output += "<tr><th>SERVICE NAME</th>"
        for env in sorted_envs:
            output += f"<th>{html.escape(env.upper())}</th>"
        output += "</tr>"
        
        # Add rows for each service
        for service_name, service_data in sorted(filtered_services.items()):
            output += "<tr>"
            
            # Highlight service name if it matches search
            service_name_html = html.escape(service_name)
            if search_query and search_query in service_name.lower():
                # Highlight the matching part
                import re
                pattern = re.compile(f'({re.escape(search_query)})', re.IGNORECASE)
                service_name_html = pattern.sub(r'<span class="highlight">\1</span>', service_name_html)
            
            output += f"<td class='service-name'>{service_name_html}</td>"
            
            for env in sorted_envs:
                version = service_data.get(env, "")
                
                # Handle different version formats
                if isinstance(version, list):
                    version_text = "<br>".join([html.escape(str(v)) for v in version]) if version else "No Version Available"
                elif isinstance(version, dict):
                    version_text = html.escape(str(version.get('version', 'N/A')))
                else:
                    version_text = html.escape(str(version)) if version else "No Version Available"
                
                # Highlight version if it matches search
                if search_query and search_query in version_text.lower():
                    import re
                    pattern = re.compile(f'({re.escape(search_query)})', re.IGNORECASE)
                    version_text = pattern.sub(r'<span class="highlight">\1</span>', version_text)
                
                # Check for special statuses
                if "Mismatch" in version_text or "mismatch" in version_text.lower():
                    mismatch_count += 1
                    output += f"<td><span class='mismatch'>{version_text}</span></td>"
                elif "No Version" in version_text or "No servers" in version_text or not version or version_text == "":
                    no_version_count += 1
                    output += f"<td><span class='no-version'>{version_text if version_text else 'No Version Available'}</span></td>"
                else:
                    output += f"<td class='version-ok'>{version_text}</td>"
            
            output += "</tr>"
        
        output += "</table>"
        
        # Add summary
        if mismatch_count > 0 or no_version_count > 0:
            output += f"""
            <div style='padding: 10px; margin: 10px 0; background-color: #fff3cd; border-left: 4px solid #ffc107; border-radius: 3px;'>
                <strong>⚠️ Summary:</strong> 
                {f'{mismatch_count} Mismatch(es)' if mismatch_count > 0 else ''} 
                {' | ' if mismatch_count > 0 and no_version_count > 0 else ''}
                {f'{no_version_count} Missing Version(s)' if no_version_count > 0 else ''}
            </div>
            """
    
    # Add results summary at the end
    if search_query:
        if total_services_found == 0:
            output += f"""
            <div style='padding: 15px; margin: 20px 0; background-color: #fff3cd; border: 2px solid #ffc107; border-radius: 5px; text-align: center;'>
                <strong>⚠️ No results found for "{html.escape(query)}"</strong>
                <p>Try a different search term or leave it empty to see all services.</p>
            </div>
            """
        else:
            output += f"""
            <div style='padding: 10px; margin: 20px 0; background-color: #d4edda; border-left: 4px solid #28a745; border-radius: 3px;'>
                <strong>✅ Found:</strong> {total_services_found} service(s) in {total_groups_shown} group(s)
            </div>
            """
    
    output += "</div>"
    return output
