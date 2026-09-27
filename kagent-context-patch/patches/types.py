import logging
from typing import Any, Callable, Literal, Optional, Union

import httpx
from agentsts.adk import ADKTokenPropagationPlugin
from google.adk.agents import Agent
from google.adk.agents.base_agent import BaseAgent
from google.adk.agents.context_cache_config import ContextCacheConfig as AdkCacheConfig
from google.adk.agents.llm_agent import ToolUnion
from google.adk.agents.remote_a2a_agent import AGENT_CARD_WELL_KNOWN_PATH, DEFAULT_TIMEOUT, RemoteA2aAgent
from google.adk.apps.app import EventsCompactionConfig
from google.adk.code_executors.base_code_executor import BaseCodeExecutor
from google.adk.models.anthropic_llm import Claude as ClaudeLLM
from google.adk.models.google_llm import Gemini as GeminiLLM
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.agent_tool import AgentTool
from google.adk.tools.mcp_tool import McpToolset, SseConnectionParams, StreamableHTTPConnectionParams
from pydantic import BaseModel, ConfigDict, Field

from kagent.adk.sandbox_code_executer import SandboxedLocalCodeExecutor

from .models import AzureOpenAI as OpenAIAzure
from .models import OpenAI as OpenAINative

logger = logging.getLogger(__name__)

# Proxy host header used for Gateway API routing when using a proxy
PROXY_HOST_HEADER = "x-kagent-host"


class HttpMcpServerConfig(BaseModel):
    params: StreamableHTTPConnectionParams
    tools: list[str] = Field(default_factory=list)


class SseMcpServerConfig(BaseModel):
    params: SseConnectionParams
    tools: list[str] = Field(default_factory=list)


class RemoteAgentConfig(BaseModel):
    name: str
    url: str
    headers: dict[str, Any] | None = None
    timeout: float = DEFAULT_TIMEOUT
    description: str = ""


class BaseLLM(BaseModel):
    model: str
    headers: dict[str, str] | None = None

    # TLS/SSL configuration (applies to all model types)
    tls_disable_verify: bool | None = None
    tls_ca_cert_path: str | None = None
    tls_disable_system_cas: bool | None = None


class OpenAI(BaseLLM):
    base_url: str | None = None
    frequency_penalty: float | None = None
    max_tokens: int | None = None
    n: int | None = None
    presence_penalty: float | None = None
    reasoning_effort: str | None = None
    seed: int | None = None
    temperature: float | None = None
    timeout: int | None = None
    top_p: float | None = None

    type: Literal["openai"]


class AzureOpenAI(BaseLLM):
    type: Literal["azure_openai"]


class Anthropic(BaseLLM):
    base_url: str | None = None

    type: Literal["anthropic"]


class GeminiVertexAI(BaseLLM):
    type: Literal["gemini_vertex_ai"]


class GeminiAnthropic(BaseLLM):
    type: Literal["gemini_anthropic"]


class Ollama(BaseLLM):
    type: Literal["ollama"]


class Gemini(BaseLLM):
    type: Literal["gemini"]


class ContextCompressionSettings(BaseModel):
    """Configuration for event compression/compaction.

    When enabled, kagent periodically summarises older conversation events
    using the agent's own configured LLM, reducing the tokens sent on each
    subsequent request. The agent's existing model and endpoint are reused,
    so this works correctly in air-gapped environments with no extra config.
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    compaction_interval: Optional[int] = Field(
        default=None,
        alias="compactionInterval",
        description="New user-invocations that must complete before compaction triggers (default: 3).",
    )
    overlap_size: Optional[int] = Field(
        default=None,
        alias="overlapSize",
        description="Preceding invocations to include in each compaction window for continuity (default: 1).",
    )


class ContextCacheSettings(BaseModel):
    """Configuration for context caching.

    When enabled, kagent caches the processed context (system prompt + tools +
    conversation) and reuses it across invocations, cutting redundant token costs.

    Note: Context caching is currently supported for Gemini models only.
    """

    model_config = ConfigDict(populate_by_name=True)

    enabled: bool
    cache_intervals: Optional[int] = Field(
        default=None,
        alias="cacheIntervals",
        description="Max invocations to reuse a cache entry before refreshing (default: 5).",
    )
    ttl_seconds: Optional[int] = Field(
        default=None,
        alias="ttlSeconds",
        description="Time-to-live for a cache entry in seconds (default: 3600).",
    )
    min_tokens: Optional[int] = Field(
        default=None,
        alias="minTokens",
        description="Minimum request tokens required to activate caching (default: 2048).",
    )


class ContextConfig(BaseModel):
    """Top-level context management configuration.

    Controls how kagent manages the conversation context sent to the LLM.
    Both mechanisms are independent and can be used together.
    """

    compression: Optional[ContextCompressionSettings] = None
    cache: Optional[ContextCacheSettings] = None


def build_adk_context_configs(ctx: Optional[ContextConfig]) -> dict:
    """Convert a ContextConfig into the ADK EventsCompactionConfig and ContextCacheConfig.

    The summarizer always uses the agent's own configured LLM (resolved at
    runtime by ADK via app.root_agent.canonical_model). This means it reuses
    the same local endpoint and credentials already configured on the agent,
    which is correct for air-gapped environments.

    Args:
        ctx: The context config parsed from config.json (may be None).

    Returns:
        A dict with keys 'events_compaction_config' and 'context_cache_config',
        each either an ADK config object or None.
    """
    result: dict = {"events_compaction_config": None, "context_cache_config": None}

    if ctx is None:
        logger.info("[context] No context config found — compression and caching are DISABLED")
        return result

    logger.info("[context] Context config received: compression=%s  cache=%s", ctx.compression, ctx.cache)

    if ctx.compression is None:
        logger.info("[context] compression: not configured")
    elif not ctx.compression.enabled:
        logger.info("[context] compression: configured but enabled=False — DISABLED")
    else:
        c = ctx.compression
        compaction_interval = c.compaction_interval or 3
        overlap_size = c.overlap_size or 1
        # summarizer=None → patched compaction.py creates a _LoggingSummarizer
        # using app.root_agent.canonical_model, reusing the agent's own local endpoint.
        result["events_compaction_config"] = EventsCompactionConfig(
            compaction_interval=compaction_interval,
            overlap_size=overlap_size,
            summarizer=None,
        )
        logger.info(
            "[context] compression ENABLED: compaction_interval=%d  overlap_size=%d"
            "  (compaction fires every %d user turn(s))",
            compaction_interval,
            overlap_size,
            compaction_interval,
        )

    if ctx.cache is None:
        logger.info("[context] cache: not configured")
    elif not ctx.cache.enabled:
        logger.info("[context] cache: configured but enabled=False — DISABLED")
    else:
        c = ctx.cache
        cache_intervals = c.cache_intervals or 5
        ttl_seconds = c.ttl_seconds or 3600
        min_tokens = c.min_tokens or 2048
        result["context_cache_config"] = AdkCacheConfig(
            cache_intervals=cache_intervals,
            ttl_seconds=ttl_seconds,
            min_tokens=min_tokens,
        )
        logger.info(
            "[context] cache ENABLED (Gemini models only): cache_intervals=%d"
            "  ttl_seconds=%d  min_tokens=%d",
            cache_intervals,
            ttl_seconds,
            min_tokens,
        )

    logger.info(
        "[context] ADK configs built: events_compaction_config=%s  context_cache_config=%s",
        result["events_compaction_config"],
        result["context_cache_config"],
    )
    return result


class AgentConfig(BaseModel):
    model: Union[OpenAI, Anthropic, GeminiVertexAI, GeminiAnthropic, Ollama, AzureOpenAI, Gemini] = Field(
        discriminator="type"
    )
    description: str
    instruction: str
    http_tools: list[HttpMcpServerConfig] | None = None  # Streamable HTTP MCP tools
    sse_tools: list[SseMcpServerConfig] | None = None  # SSE MCP tools
    remote_agents: list[RemoteAgentConfig] | None = None  # remote agents
    execute_code: bool | None = None
    context: Optional[ContextConfig] = None  # context management (compression + caching)

    def to_agent(self, name: str, sts_integration: Optional[ADKTokenPropagationPlugin] = None) -> Agent:
        if name is None or not str(name).strip():
            raise ValueError("Agent name must be a non-empty string.")
        tools: list[ToolUnion] = []
        header_provider = None
        if sts_integration:
            header_provider = sts_integration.header_provider
        if self.http_tools:
            for http_tool in self.http_tools:  # add http tools
                # If the proxy is configured, the url and headers are set in the json configuration
                tools.append(
                    McpToolset(
                        connection_params=http_tool.params, tool_filter=http_tool.tools, header_provider=header_provider
                    )
                )
        if self.sse_tools:
            for sse_tool in self.sse_tools:  # add sse tools
                # If the proxy is configured, the url and headers are set in the json configuration
                tools.append(
                    McpToolset(
                        connection_params=sse_tool.params, tool_filter=sse_tool.tools, header_provider=header_provider
                    )
                )
        if self.remote_agents:
            for remote_agent in self.remote_agents:  # Add remote agents as tools
                # Prepare httpx client parameters
                timeout = httpx.Timeout(timeout=remote_agent.timeout)
                headers: dict[str, str] | None = remote_agent.headers
                base_url: str | None = None
                event_hooks: dict[str, list[Callable[[httpx.Request], None]]] | None = None

                # If headers includes the proxy host header, it means we're using a proxy
                # RemoteA2aAgent may use URLs from agent card response, so we need to
                # rewrite all request URLs to use the proxy URL while preserving the proxy host header
                if remote_agent.headers and PROXY_HOST_HEADER in remote_agent.headers:
                    # Parse the proxy URL to extract base URL
                    from urllib.parse import urlparse as parse_url

                    parsed_proxy = parse_url(remote_agent.url)
                    proxy_base = f"{parsed_proxy.scheme}://{parsed_proxy.netloc}"
                    target_host = remote_agent.headers[PROXY_HOST_HEADER]

                    # Event hook to rewrite request URLs to use proxy while preserving the proxy host header
                    # Note: Relative paths are handled by base_url below, so they'll already point to proxy_base
                    def make_rewrite_url_to_proxy(proxy_base: str, target_host: str) -> Callable[[httpx.Request], None]:
                        async def rewrite_url_to_proxy(request: httpx.Request) -> None:
                            parsed = parse_url(str(request.url))
                            proxy_netloc = parse_url(proxy_base).netloc

                            # If URL is absolute and points to a different host, rewrite to the proxy base URL
                            if parsed.netloc and parsed.netloc != proxy_netloc:
                                # This is an absolute URL pointing to the target service, rewrite it
                                new_url = f"{proxy_base}{parsed.path}"
                                if parsed.query:
                                    new_url += f"?{parsed.query}"
                                request.url = httpx.URL(new_url)

                            # Always set proxy host header for Gateway API routing
                            request.headers[PROXY_HOST_HEADER] = target_host

                        return rewrite_url_to_proxy

                    # Set base_url so relative paths work correctly with httpx
                    # httpx requires either base_url or absolute URLs - relative paths will fail without base_url
                    base_url = proxy_base
                    event_hooks = {"request": [make_rewrite_url_to_proxy(proxy_base, target_host)]}

                # Note: httpx doesn't accept None for base_url/event_hooks, so we only pass the parameters if set
                if base_url and event_hooks:
                    client = httpx.AsyncClient(
                        timeout=timeout,
                        headers=headers,
                        base_url=base_url,
                        event_hooks=event_hooks,
                    )
                elif headers:
                    client = httpx.AsyncClient(
                        timeout=timeout,
                        headers=headers,
                    )
                else:
                    client = httpx.AsyncClient(
                        timeout=timeout,
                    )

                remote_a2a_agent = RemoteA2aAgent(
                    name=remote_agent.name,
                    agent_card=f"{remote_agent.url}{AGENT_CARD_WELL_KNOWN_PATH}",
                    description=remote_agent.description,
                    httpx_client=client,
                )

                tools.append(AgentTool(agent=remote_a2a_agent))

        extra_headers = self.model.headers or {}

        code_executor = SandboxedLocalCodeExecutor() if self.execute_code else None

        if self.model.type == "openai":
            model = OpenAINative(
                type="openai",
                base_url=self.model.base_url,
                default_headers=extra_headers,
                frequency_penalty=self.model.frequency_penalty,
                max_tokens=self.model.max_tokens,
                model=self.model.model,
                n=self.model.n,
                presence_penalty=self.model.presence_penalty,
                reasoning_effort=self.model.reasoning_effort,
                seed=self.model.seed,
                temperature=self.model.temperature,
                timeout=self.model.timeout,
                top_p=self.model.top_p,
                # TLS configuration
                tls_disable_verify=self.model.tls_disable_verify,
                tls_ca_cert_path=self.model.tls_ca_cert_path,
                tls_disable_system_cas=self.model.tls_disable_system_cas,
            )
        elif self.model.type == "anthropic":
            model = LiteLlm(
                model=f"anthropic/{self.model.model}", base_url=self.model.base_url, extra_headers=extra_headers
            )
        elif self.model.type == "gemini_vertex_ai":
            model = GeminiLLM(model=self.model.model)
        elif self.model.type == "gemini_anthropic":
            model = ClaudeLLM(model=self.model.model)
        elif self.model.type == "ollama":
            model = LiteLlm(model=f"ollama_chat/{self.model.model}", extra_headers=extra_headers)
        elif self.model.type == "azure_openai":
            model = OpenAIAzure(
                model=self.model.model,
                type="azure_openai",
                default_headers=extra_headers,
                # TLS configuration
                tls_disable_verify=self.model.tls_disable_verify,
                tls_ca_cert_path=self.model.tls_ca_cert_path,
                tls_disable_system_cas=self.model.tls_disable_system_cas,
            )
        elif self.model.type == "gemini":
            model = self.model.model
        else:
            raise ValueError(f"Invalid model type: {self.model.type}")
        return Agent(
            name=name,
            model=model,
            description=self.description,
            instruction=self.instruction,
            tools=tools,
            code_executor=code_executor,
        )
