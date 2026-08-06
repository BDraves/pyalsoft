FROM quay.io/pypa/manylinux_2_28_x86_64:2026.03.20-1

RUN dnf install -y --setopt=install_weak_deps=False \
        alsa-lib-devel \
        pulseaudio-libs-devel \
    && dnf clean all \
    && rm -rf /var/cache/dnf \
    && touch /opt/pyalsoft-manylinux-ready
