# src/coordinator/mcp_client_stdio.py
# Brave MCP Client using STDIO transport with ephemeral Docker containers
# Official MCP pattern: spawn containers on-demand with `docker run -i --rm`

from __future__ import annotations

import os
import json
import logging
import subprocess
from typing import Dict, List, Optional, Any

from .models.mcp_models import (
    SearchResult,
    MCPConnectionError,
    MCPTimeoutError,
)

# Configure logging
logger = logging.getLogger(__name__)


class BraveMCPClientStdio:
    """
    STDIO-based client for Brave Search MCP using ephemeral Docker containers.

    This implements the official MCP + Docker pattern:
    - Each request spawns a new container: `docker run -i --rm`
    - Container processes request via STDIN, returns via STDOUT, then dies
    - No Docker socket mounting or long-running containers needed
    - Clean, stateless, and follows MCP conventions

    Based on: https://github.com/brave/brave-search-mcp-server
    """

    def __init__(
        self,
        image: str = "docker.io/mcp/brave-search",
        api_key: Optional[str] = None,
        timeout: int = 30,
        max_results: int = 5,
        safesearch: str = "moderate"
    ):
        """
        Initialize Brave MCP STDIO client.

        Args:
            image: Docker image to use (default: docker.io/mcp/brave-search)
            api_key: Brave Search API key (defaults to BRAVE_API_KEY env var)
            timeout: Timeout in seconds for search operations
            max_results: Maximum number of search results to return
            safesearch: Safe search filter ('off', 'moderate', 'strict')
        """
        self.image = image
        self.api_key = api_key or os.getenv("BRAVE_API_KEY")
        self.timeout = timeout
        self.max_results = max_results
        self.safesearch = safesearch

        if not self.api_key:
            logger.warning("No BRAVE_API_KEY provided - searches will fail")

        logger.info(
            f"Initialized BraveMCPClientStdio: image={image}, "
            f"timeout={timeout}s, max_results={max_results}"
        )

    def _spawn_mcp_container(self, request: Dict[str, Any]) -> str:
        """
        Spawn ephemeral MCP container to process a single request.

        This is the official MCP pattern: spawn container, send JSON-RPC via STDIN,
        receive response via STDOUT, container dies automatically.

        SECURITY: Resource limits applied to prevent DoS attacks:
        - Memory: 256MB max (prevents memory exhaustion)
        - CPU: 0.5 cores max (prevents CPU starvation)
        - PIDs: 100 max (prevents fork bombs)
        - Labels: Enables orphan container detection

        Args:
            request: JSON-RPC request object

        Returns:
            Raw stdout from MCP server

        Raises:
            MCPConnectionError: If container spawn fails
            MCPTimeoutError: If operation times out
        """
        # Construct docker run command with resource limits
        # -i: interactive (enables STDIN)
        # --rm: remove container after exit
        # -e: pass environment variable
        # --memory: limit memory to prevent exhaustion
        # --cpus: limit CPU to prevent starvation
        # --pids-limit: limit processes to prevent fork bombs
        # --label: enable orphan detection and cleanup
        cmd = [
            "docker", "run", "-i", "--rm",
            "--memory=256m",
            "--cpus=0.5",
            "--pids-limit=100",
            "--label=mcp.coordinator.ephemeral=true",
            "--label=mcp.coordinator.service=brave-search",
            # Bare "-e NAME" (no "=value") — Docker inherits the value from THIS
            # process's environment, so the key never enters argv. It used to be
            # interpolated here, and `subprocess.TimeoutExpired.__str__` embeds the
            # full command unconditionally (CPython has no redaction hook), so a
            # single container timeout wrote the live key into the backend log.
            # The debug line below joins `cmd` too, which was a second path to the
            # same leak. Both are closed by keeping the value out of the list.
            "-e", "BRAVE_API_KEY",
            self.image
        ]

        # The value travels in the child's environment instead of its arguments.
        child_env = {**os.environ, "BRAVE_API_KEY": self.api_key or ""}

        # Convert request to JSON string
        stdin_data = json.dumps(request) + "\n"

        logger.debug(f"Spawning ephemeral container: {' '.join(cmd)}")
        logger.debug(f"Request: {stdin_data.strip()}")

        process = None
        try:
            # Spawn container with STDIN/STDOUT pipes
            process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=child_env,
            )

            # Send request and wait for response
            stdout, stderr = process.communicate(
                input=stdin_data,
                timeout=self.timeout
            )

            if process.returncode != 0:
                logger.error(f"MCP container exited with code {process.returncode}")
                logger.error(f"stderr: {stderr}")
                raise MCPConnectionError(
                    f"MCP container failed: {stderr or 'Unknown error'}"
                )

            logger.debug(f"Response: {stdout[:500]}...")
            return stdout

        except subprocess.TimeoutExpired:
            # Guaranteed cleanup: kill process and wait for termination
            if process:
                logger.warning(f"MCP operation timed out after {self.timeout}s, killing container...")
                process.kill()
                try:
                    # Wait for process to die (max 5 seconds)
                    process.wait(timeout=5)
                    logger.info("Container killed successfully")
                except subprocess.TimeoutExpired:
                    # If still alive after kill, log error (--rm will cleanup eventually)
                    logger.error("Container did not die after kill signal - orphan cleanup required")
            raise MCPTimeoutError(f"Operation timed out after {self.timeout}s")

        except FileNotFoundError:
            logger.error("Docker command not found - is Docker installed?")
            raise MCPConnectionError("Docker command not available")

        except Exception as e:
            # Cleanup on any error
            if process and process.poll() is None:
                logger.warning("Cleaning up container due to unexpected error...")
                try:
                    process.kill()
                    process.wait(timeout=5)
                except Exception as cleanup_error:
                    logger.error(f"Cleanup failed: {cleanup_error}")

            logger.error(f"Failed to spawn MCP container: {e}")
            raise MCPConnectionError(f"Failed to spawn container: {e}")

    def search_web(
        self,
        query: str,
        count: Optional[int] = None,
        country: Optional[str] = None,
        search_lang: Optional[str] = None,
        freshness: Optional[str] = None,
        safesearch: Optional[str] = None
    ) -> List[SearchResult]:
        """
        Search the web using Brave Search.

        Args:
            query: Search query (max 400 chars, 50 words)
            count: Number of results (1-20, defaults to max_results)
            country: Country code (e.g., 'US', 'GB')
            search_lang: Language code (e.g., 'en', 'de')
            freshness: Time filter ('pd'=day, 'pw'=week, 'pm'=month, 'py'=year)
            safesearch: Per-call override (off|moderate|strict); None uses the
                client default (ADR-009: the WEB_SAFESEARCH_DEFAULT-driven value
                is passed here so an uncensored persona actually gets off).

        Returns:
            List of SearchResult objects

        Raises:
            ValueError: If query is invalid
            MCPError: If search fails
        """
        # Validate query
        if not query or not query.strip():
            raise ValueError("Search query cannot be empty")

        query = query.strip()
        if len(query) > 400:
            logger.warning(f"Query too long ({len(query)} chars), truncating to 400")
            query = query[:400]

        # Build tool arguments
        arguments = {
            "query": query,
            "count": count or self.max_results,
            "safesearch": (safesearch or self.safesearch)
        }

        # Add optional parameters
        if country:
            arguments["country"] = country
        if search_lang:
            arguments["search_lang"] = search_lang
        if freshness:
            arguments["freshness"] = freshness

        logger.info(f"Searching Brave (ephemeral): query='{query}', count={arguments['count']}")

        try:
            # Build JSON-RPC request
            request = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "brave_web_search",
                    "arguments": arguments
                }
            }

            # Spawn ephemeral container and get response
            stdout = self._spawn_mcp_container(request)

            # Parse results
            results = self._parse_mcp_response(stdout)
            logger.info(f"Received {len(results)} search results (ephemeral)")

            return results

        except Exception as e:
            logger.error(f"Brave search failed: {e}")
            raise

    def _parse_mcp_response(self, stdout: str) -> List[SearchResult]:
        """
        Parse MCP STDIO response into SearchResult objects.

        MCP servers send multiple JSON-RPC messages (newline-delimited).

        Args:
            stdout: Raw stdout from MCP server

        Returns:
            List of parsed SearchResult objects
        """
        results = []

        # MCP STDIO sends multiple JSON-RPC messages (newline-delimited)
        for line in stdout.strip().split("\n"):
            if not line.strip():
                continue

            try:
                msg = json.loads(line)

                # Look for tool result
                if "result" in msg:
                    result = msg["result"]
                    content = result.get("content", [])

                    for item in content:
                        if item.get("type") == "text":
                            text = item.get("text", "")

                            # Parse JSON search result
                            if text.startswith("{"):
                                data = json.loads(text)

                                # Direct result format
                                if "url" in data and "title" in data:
                                    results.append(SearchResult(
                                        title=data.get("title", "Untitled"),
                                        url=data.get("url", ""),
                                        description=data.get("description", ""),
                                        age=data.get("age")
                                    ))
                                # Wrapped format
                                elif "web" in data and "results" in data["web"]:
                                    for web_result in data["web"]["results"]:
                                        results.append(SearchResult(
                                            title=web_result.get("title", "Untitled"),
                                            url=web_result.get("url", ""),
                                            description=web_result.get("description", ""),
                                            age=web_result.get("age")
                                        ))
                                # Array format
                                elif "results" in data:
                                    for web_result in data["results"]:
                                        results.append(SearchResult(
                                            title=web_result.get("title", "Untitled"),
                                            url=web_result.get("url", ""),
                                            description=web_result.get("description", ""),
                                            age=web_result.get("age")
                                        ))

            except json.JSONDecodeError:
                logger.warning(f"Failed to parse line as JSON: {line[:100]}")
                continue

        return results

    def health_check(self) -> bool:
        """
        Check if Docker is available and can pull/run the MCP image.

        Returns:
            True if Docker is accessible, False otherwise
        """
        try:
            result = subprocess.run(
                ["docker", "version"],
                capture_output=True,
                text=True,
                timeout=5
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            return False


def get_brave_client_stdio() -> BraveMCPClientStdio:
    """
    Factory function to create a BraveMCPClientStdio with environment configuration.

    Returns:
        Configured BraveMCPClientStdio instance
    """
    return BraveMCPClientStdio(
        image=os.getenv("BRAVE_MCP_IMAGE", "docker.io/mcp/brave-search"),
        api_key=os.getenv("BRAVE_API_KEY"),
        timeout=int(os.getenv("BRAVE_SEARCH_TIMEOUT", "30")),
        max_results=int(os.getenv("BRAVE_MAX_RESULTS", "5")),
        safesearch=os.getenv("BRAVE_SAFESEARCH", "moderate")
    )


# Example usage / test code
if __name__ == "__main__":
    import time

    # Configure logging for testing
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    try:
        print("Testing Brave MCP STDIO Client (Ephemeral Pattern)...")
        print("=" * 60)

        client = get_brave_client_stdio()

        # Test health check
        print("\nChecking Docker availability...")
        if client.health_check():
            print("✅ Docker is available")
        else:
            print("⚠️  Docker not available")

        # Test search
        query = "weather Brugg Switzerland"
        print(f"\nSearching for: {query}")
        print("-" * 60)

        start_time = time.time()
        results = client.search_web(query, count=3)
        elapsed = time.time() - start_time

        print(f"\n✅ Found {len(results)} results in {elapsed:.2f}s:\n")
        for i, result in enumerate(results, 1):
            print(f"{i}. {result.title}")
            print(f"   URL: {result.url}")
            print(f"   {result.description[:100]}...")
            if result.age:
                print(f"   Age: {result.age}")
            print()

        print("=" * 60)
        print("✅ Test completed successfully!")

    except Exception as e:
        print(f"\n❌ Test failed: {e}")
        logger.error("Test failed", exc_info=True)
