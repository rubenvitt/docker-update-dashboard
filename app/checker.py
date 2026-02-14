import asyncio
import socket
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import docker
import httpx


@dataclass
class CacheEntry:
    data: dict
    timestamp: float


class DockerUpdateChecker:
    ACCEPT_HEADERS = (
        "application/vnd.docker.distribution.manifest.list.v2+json, "
        "application/vnd.docker.distribution.manifest.v2+json, "
        "application/vnd.oci.image.index.v1+json, "
        "application/vnd.oci.image.manifest.v1+json"
    )

    def __init__(self, check_interval: int = 300):
        self.client = docker.from_env()
        self.check_interval = check_interval
        self._cache: dict[str, CacheEntry] = {}
        self._hostname = socket.gethostname()

    def clear_cache(self):
        self._cache.clear()

    def _parse_image_reference(self, image_name: str) -> tuple[str, str, str]:
        """Parse image reference into (registry, repository, tag).

        Rules:
        - No `/` → library image on Docker Hub
        - First segment contains `.` or `:` → custom registry
        - Otherwise → Docker Hub user image
        """
        if ":" in image_name and "/" in image_name.split(":")[0]:
            # Separate tag from the rest
            last_colon = image_name.rsplit(":", 1)
            name_part, tag = last_colon[0], last_colon[1]
        elif ":" in image_name and "/" not in image_name.split(":")[0]:
            name_part, tag = image_name.rsplit(":", 1)
        else:
            name_part = image_name
            tag = "latest"

        if "/" not in name_part:
            return "registry-1.docker.io", f"library/{name_part}", tag

        parts = name_part.split("/", 1)
        first_segment = parts[0]

        if "." in first_segment or ":" in first_segment:
            return first_segment, parts[1], tag

        return "registry-1.docker.io", name_part, tag

    def _get_local_digest(self, image) -> Optional[str]:
        repo_digests = image.attrs.get("RepoDigests", [])
        if not repo_digests:
            return None
        # RepoDigests format: ["repo@sha256:abc..."]
        return repo_digests[0].split("@", 1)[-1] if "@" in repo_digests[0] else None

    def _extract_ports(self, container) -> list[str]:
        ports_config = container.attrs.get("NetworkSettings", {}).get("Ports") or {}
        seen = set()
        result = []
        for container_port, bindings in ports_config.items():
            if bindings:
                for binding in bindings:
                    host_port = binding.get("HostPort", "")
                    if host_port:
                        display = f"{host_port}\u2192{container_port}"
                        if display not in seen:
                            seen.add(display)
                            result.append(display)
            else:
                if container_port not in seen:
                    seen.add(container_port)
                    result.append(container_port)
        return result

    async def _get_docker_hub_token(self, client: httpx.AsyncClient, repository: str) -> str:
        url = (
            f"https://auth.docker.io/token"
            f"?service=registry.docker.io&scope=repository:{repository}:pull"
        )
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.json()["token"]

    async def _parse_www_authenticate(self, header: str) -> dict[str, str]:
        """Parse WWW-Authenticate header to extract realm, service, scope."""
        params = {}
        # Format: Bearer realm="...",service="...",scope="..."
        header = header.replace("Bearer ", "")
        for part in header.split(","):
            if "=" in part:
                key, value = part.split("=", 1)
                params[key.strip()] = value.strip().strip('"')
        return params

    async def _get_custom_registry_token(
        self, client: httpx.AsyncClient, registry: str, repository: str
    ) -> Optional[str]:
        """Authenticate against a custom registry using the v2 API challenge flow."""
        try:
            resp = await client.get(f"https://{registry}/v2/")
            if resp.status_code != 401:
                return None

            www_auth = resp.headers.get("www-authenticate", "")
            if not www_auth:
                return None

            params = await self._parse_www_authenticate(www_auth)
            realm = params.get("realm")
            if not realm:
                return None

            token_params = {}
            if "service" in params:
                token_params["service"] = params["service"]
            token_params["scope"] = f"repository:{repository}:pull"

            token_resp = await client.get(realm, params=token_params)
            token_resp.raise_for_status()
            return token_resp.json().get("token")
        except Exception:
            return None

    async def _get_remote_digest(
        self, client: httpx.AsyncClient, registry: str, repository: str, tag: str
    ) -> Optional[str]:
        if registry == "registry-1.docker.io":
            token = await self._get_docker_hub_token(client, repository)
        else:
            token = await self._get_custom_registry_token(client, registry, repository)

        headers = {"Accept": self.ACCEPT_HEADERS}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        url = f"https://{registry}/v2/{repository}/manifests/{tag}"
        resp = await client.head(url, headers=headers)
        resp.raise_for_status()

        return resp.headers.get("Docker-Content-Digest")

    def _resolve_image_name(self, container) -> Optional[str]:
        """Get the image reference for a container.

        Prefers current image tags but falls back to Config.Image
        (the name used when the container was created). This handles
        the case where the tag moved to a newer pulled image.
        """
        tags = container.image.tags
        if tags:
            return tags[0]
        # Tag was reassigned to a newer image – use the original reference
        config_image = container.attrs.get("Config", {}).get("Image", "")
        if config_image and not config_image.startswith("sha256:"):
            return config_image
        return None

    async def _check_container(self, container) -> dict:
        image_name = self._resolve_image_name(container)
        if not image_name:
            return self._build_container_info(
                container,
                error="Kein Image-Tag verfügbar",
            )

        # Check cache
        cached = self._cache.get(image_name)
        if cached and (time.time() - cached.timestamp) < self.check_interval:
            info = cached.data.copy()
            info.update(self._build_container_base(container))
            return info

        registry, repository, tag = self._parse_image_reference(image_name)
        local_digest = self._get_local_digest(container.image)

        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                remote_digest = await self._get_remote_digest(client, registry, repository, tag)

            update_available = (
                local_digest is not None
                and remote_digest is not None
                and local_digest != remote_digest
            )

            result = self._build_container_info(
                container,
                update_available=update_available,
                local_digest=local_digest,
                remote_digest=remote_digest,
            )
        except Exception as e:
            result = self._build_container_info(
                container,
                local_digest=local_digest,
                error=str(e),
            )

        # Cache the digest-related fields
        cache_data = {
            k: result[k]
            for k in ("update_available", "local_digest", "remote_digest", "error", "checked_at")
        }
        self._cache[image_name] = CacheEntry(data=cache_data, timestamp=time.time())

        return result

    def _build_container_base(self, container) -> dict:
        return {
            "id": container.short_id,
            "name": container.name,
            "image": self._resolve_image_name(container) or str(container.image.id)[:19],
            "status": container.status,
            "state": container.attrs.get("State", {}).get("Status", "unknown"),
            "ports": self._extract_ports(container),
            "started_at": container.attrs.get("State", {}).get("StartedAt", ""),
            "created": container.attrs.get("Created", ""),
        }

    def _build_container_info(
        self,
        container,
        update_available: bool = False,
        local_digest: Optional[str] = None,
        remote_digest: Optional[str] = None,
        error: Optional[str] = None,
    ) -> dict:
        info = self._build_container_base(container)
        info.update({
            "update_available": update_available,
            "local_digest": local_digest,
            "remote_digest": remote_digest,
            "error": error,
            "checked_at": datetime.now().isoformat(),
        })
        return info

    def _is_self(self, container) -> bool:
        """Check if the given container is the dashboard itself."""
        return (
            container.id.startswith(self._hostname)
            or self._hostname.startswith(container.short_id)
        )

    def _extract_run_config(self, container) -> tuple[dict, dict]:
        """Extract run kwargs and extra networks from a container."""
        attrs = container.attrs
        host_config = attrs.get("HostConfig", {})
        config = attrs.get("Config", {})
        network_settings = attrs.get("NetworkSettings", {})

        run_kwargs = {
            "name": container.name,
            "detach": True,
            "environment": config.get("Env") or [],
            "labels": config.get("Labels") or {},
        }

        cmd = config.get("Cmd")
        if cmd:
            run_kwargs["command"] = cmd
        entrypoint = config.get("Entrypoint")
        if entrypoint:
            run_kwargs["entrypoint"] = entrypoint
        working_dir = config.get("WorkingDir")
        if working_dir:
            run_kwargs["working_dir"] = working_dir
        user = config.get("User")
        if user:
            run_kwargs["user"] = user
        restart_policy = host_config.get("RestartPolicy")
        if restart_policy and restart_policy.get("Name"):
            run_kwargs["restart_policy"] = restart_policy
        port_bindings = host_config.get("PortBindings")
        if port_bindings:
            run_kwargs["ports"] = port_bindings
        binds = host_config.get("Binds")
        if binds:
            run_kwargs["volumes"] = binds
        network_mode = host_config.get("NetworkMode")
        if network_mode:
            run_kwargs["network_mode"] = network_mode
        if host_config.get("Privileged"):
            run_kwargs["privileged"] = True
        hostname = config.get("Hostname")
        if hostname and hostname != container.short_id:
            run_kwargs["hostname"] = hostname

        extra_networks = {}
        networks = network_settings.get("Networks") or {}
        for net_name, net_conf in networks.items():
            if net_name == network_mode or net_name == "bridge":
                continue
            extra_networks[net_name] = {
                k: net_conf.get(k)
                for k in ("IPAMConfig", "Aliases")
                if net_conf.get(k)
            }

        return run_kwargs, extra_networks

    def _pull_image_stream(self, image_name: str):
        """Generator that yields pull progress events."""
        if ":" in image_name:
            repo, tag = image_name.rsplit(":", 1)
        else:
            repo, tag = image_name, "latest"

        yield {"type": "log", "message": f"Image wird heruntergeladen: {image_name}"}

        pull_output = self.client.api.pull(repo, tag=tag, stream=True, decode=True)
        for chunk in pull_output:
            status = chunk.get("status", "")
            layer_id = chunk.get("id", "")
            if not status:
                continue
            if status in ("Downloading", "Extracting", "Verifying Checksum",
                          "Waiting", "Pulling fs layer"):
                continue
            if layer_id and status in ("Pull complete", "Already exists"):
                yield {"type": "pull_progress", "message": f"{layer_id[:12]}: {status}"}
            elif status.startswith("Digest:"):
                yield {"type": "pull_progress", "message": status}
            elif status.startswith("Status:"):
                yield {"type": "log", "message": status, "icon": "success"}

    def _reconnect_networks(self, new_container, extra_networks: dict):
        """Generator that yields log events while reconnecting networks."""
        for net_name, net_opts in extra_networks.items():
            yield {"type": "log", "message": f"Netzwerk wird verbunden: {net_name}"}
            try:
                network = self.client.networks.get(net_name)
                connect_kwargs = {}
                if net_opts.get("Aliases"):
                    connect_kwargs["aliases"] = net_opts["Aliases"]
                network.connect(new_container, **connect_kwargs)
                yield {"type": "log", "message": f"Netzwerk verbunden: {net_name}", "icon": "success"}
            except Exception as e:
                yield {"type": "log", "message": f"Netzwerk-Fehler ({net_name}): {e}", "icon": "error"}

    def update_container_stream(self, container_id: str):
        """Generator that yields SSE log events while updating a container."""
        try:
            container = self.client.containers.get(container_id)
        except docker.errors.NotFound:
            yield {"type": "complete", "success": False, "message": "Container nicht gefunden"}
            return

        container_name = container.name

        if self._is_self(container):
            yield {"type": "log", "message": f"Self-Update erkannt: {container_name}"}
            yield from self._self_update_stream(container)
            return

        yield {"type": "log", "message": f"Container gefunden: {container_name}"}

        try:
            image_name = self._resolve_image_name(container)
            if not image_name:
                yield {"type": "complete", "success": False, "message": "Kein Image-Tag verfügbar"}
                return

            # Pull
            yield from self._pull_image_stream(image_name)

            # Extract config
            yield {"type": "log", "message": "Konfiguration wird gesichert…"}
            run_kwargs, extra_networks = self._extract_run_config(container)

            # Stop & remove
            yield {"type": "log", "message": "Container wird gestoppt…"}
            container.stop(timeout=30)
            yield {"type": "log", "message": "Container gestoppt", "icon": "success"}

            yield {"type": "log", "message": "Container wird entfernt…"}
            container.remove()
            yield {"type": "log", "message": "Container entfernt", "icon": "success"}

            # Create new
            yield {"type": "log", "message": "Neuer Container wird erstellt…"}
            new_container = self.client.containers.run(image_name, **run_kwargs)
            yield {"type": "log", "message": f"Container gestartet: {new_container.short_id}", "icon": "success"}

            # Networks
            yield from self._reconnect_networks(new_container, extra_networks)

            self._cache.pop(image_name, None)
            yield {"type": "complete", "success": True, "message": "Update erfolgreich abgeschlossen"}

        except Exception as e:
            yield {"type": "complete", "success": False, "message": str(e)}

    def _self_update_stream(self, container):
        """Handle self-update: rename old, start new, spawn cleanup."""
        container_name = container.name
        old_id = container.id

        try:
            image_name = self._resolve_image_name(container)
            if not image_name:
                yield {"type": "complete", "success": False, "message": "Kein Image-Tag verfügbar"}
                return

            # Pull new image
            yield from self._pull_image_stream(image_name)

            # Extract config
            yield {"type": "log", "message": "Konfiguration wird gesichert…"}
            run_kwargs, extra_networks = self._extract_run_config(container)

            # Rename current container to free up the name
            temp_name = f"{container_name}-old"
            yield {"type": "log", "message": f"Container wird umbenannt: {container_name} → {temp_name}"}
            container.rename(temp_name)
            yield {"type": "log", "message": "Container umbenannt", "icon": "success"}

            # Create + start new container with the original name
            yield {"type": "log", "message": "Neuer Container wird erstellt…"}
            new_container = self.client.containers.run(image_name, **run_kwargs)
            yield {"type": "log", "message": f"Neuer Container gestartet: {new_container.short_id}", "icon": "success"}

            # Reconnect extra networks on the new container
            yield from self._reconnect_networks(new_container, extra_networks)

            # Spawn a fire-and-forget cleanup container that stops + removes the old one
            yield {"type": "log", "message": "Cleanup wird gestartet…"}
            cleanup_script = (
                "import docker, time; "
                "time.sleep(5); "
                f"c = docker.from_env().containers.get('{old_id}'); "
                "c.stop(timeout=30); "
                "c.remove()"
            )
            self.client.containers.run(
                image_name,
                command=["python", "-c", cleanup_script],
                volumes={"/var/run/docker.sock": {"bind": "/var/run/docker.sock"}},
                detach=True,
                auto_remove=True,
                name=f"{container_name}-cleanup",
            )
            yield {"type": "log", "message": "Alter Container wird in 5s entfernt", "icon": "success"}

            self._cache.pop(image_name, None)
            yield {
                "type": "complete",
                "success": True,
                "message": "Self-Update abgeschlossen – Seite wird neu geladen",
                "reload": True,
            }

        except Exception as e:
            yield {"type": "complete", "success": False, "message": str(e)}

    async def get_all_containers(self) -> list[dict]:
        containers = self.client.containers.list()
        tasks = [self._check_container(c) for c in containers]
        return await asyncio.gather(*tasks)
