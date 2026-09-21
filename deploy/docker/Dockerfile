# Build one provider per image. Login happens at runtime, never during image build.
FROM node:22-bookworm-slim
ARG PROVIDER
ARG AGENT_UID=1000
ARG AGENT_GID=1000
USER root
RUN apt-get update && apt-get install -y --no-install-recommends python3 git curl ca-certificates bash coreutils \
    && rm -rf /var/lib/apt/lists/* \
    && (getent group ${AGENT_GID} >/dev/null || groupmod -g ${AGENT_GID} node) && usermod -u ${AGENT_UID} -g ${AGENT_GID} -d /home/agent node \
    && mkdir -p /opt/cli /home/agent && chown -R ${AGENT_UID}:${AGENT_GID} /opt/cli /home/agent
USER ${AGENT_UID}:${AGENT_GID}
ENV HOME=/opt/cli
SHELL ["/bin/bash", "-o", "pipefail", "-c"]
RUN case "$PROVIDER" in \
      grok) curl -fsSL https://x.ai/cli/install.sh | bash ;; \
      devin) curl -fsSL https://cli.devin.ai/install.sh -o /tmp/devin-install.sh \
        && python3 -c 'from pathlib import Path;p=Path("/tmp/devin-install.sh");s=p.read_text();line=chr(34)+"$VERSION_DIR/bin/$COMPILED_BIN_NAME"+chr(34)+" setup";assert s.rstrip().endswith(line), "Installer changed: review before building";p.write_text(s.rstrip()[:-len(line)]+"\n")' \
        && bash /tmp/devin-install.sh && rm /tmp/devin-install.sh ;; \
      cursor) curl -fsSL https://cursor.com/install | bash ;; \
      *) echo 'PROVIDER must be grok, devin or cursor' >&2; exit 1 ;; \
    esac
ENV PATH="/opt/cli/.grok/bin:/opt/cli/.local/bin:/usr/local/bin:/usr/bin:/bin" HOME=/home/agent
WORKDIR /work
CMD ["bash"]
