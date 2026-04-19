# Packer template for Starsector worker AMI.
# Bakes the game, combat-harness mod, uv venv, Tailscale client, and XRandR
# warmup dependencies. Builds in us-east-1; `bake_image.sh` then runs
# `aws ec2 copy-image` to produce the us-east-2 copy.
#
# Usage:
#   scripts/cloud/bake_image.sh
#
# See docs/specs/22-cloud-deployment.md for the full package rationale.

packer {
  required_plugins {
    amazon = {
      version = ">= 1.2"
      source  = "github.com/hashicorp/amazon"
    }
  }
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "instance_type" {
  type    = string
  default = "c7a.2xlarge"
}

variable "ami_name_prefix" {
  type    = string
  default = "starsector-worker"
}

variable "game_dir" {
  type        = string
  default     = "./game/starsector"
  description = "Local path to the Starsector installation (host side)."
}

variable "vpc_id" {
  type        = string
  default     = "vpc-08c429b59b0728a0c"
  description = "VPC for the Packer builder instance (us-east-1)."
}

variable "subnet_id" {
  type        = string
  default     = "subnet-01745d2ce8253cc8b"
  description = "Public-IP-capable subnet inside vpc_id."
}

variable "project_src" {
  type        = string
  default     = "./src"
  description = "Local path to Python project src/ (host side)."
}

# GameVersion + ModCommitSha are read directly from the committed manifest
# so they cannot desync from what the orchestrator preflights against. The
# jar's generateBuildInfo task stamps the git SHA into the manifest; Packer
# mirrors that value into the AMI tag; CampaignManager._check_manifest_and_ami_tags
# compares the two. One file, one source of truth.
locals {
  manifest       = jsondecode(file("${path.root}/../../../game/starsector/manifest.json"))
  game_version   = local.manifest.constants.game_version
  mod_commit_sha = local.manifest.constants.mod_commit_sha
}

source "amazon-ebs" "worker" {
  region                      = var.region
  instance_type               = var.instance_type
  ami_name                    = "${var.ami_name_prefix}-{{timestamp}}"
  ssh_username                = "ubuntu"
  vpc_id                      = var.vpc_id
  subnet_id                   = var.subnet_id
  associate_public_ip_address = true

  source_ami_filter {
    filters = {
      name                = "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-*"
      root-device-type    = "ebs"
      virtualization-type = "hvm"
    }
    most_recent = true
    owners      = ["099720109477"] # Canonical
  }

  tags = {
    Project      = "starsector"
    Role         = "worker-image"
    GameVersion  = local.game_version
    ModCommitSha = local.mod_commit_sha
  }
}

build {
  sources = ["source.amazon-ebs.worker"]

  # --- System packages ---
  provisioner "shell" {
    inline = [
      "set -e",
      "sudo apt-get update",
      "sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \\",
      "  xvfb xdotool x11-xserver-utils \\",
      "  libgl1 libasound2t64 libxi6 libxrender1 libxtst6 libxrandr2 \\",
      "  libxcursor1 libxxf86vm1 libopenal1 \\",
      "  rsync curl python3 python3-pip",
    ]
  }

  # --- Null ALSA to prevent OpenAL error dialog on headless VMs ---
  provisioner "shell" {
    inline = [
      "echo 'pcm.!default { type null }' | sudo tee /etc/asound.conf",
      "echo 'ctl.!default { type null }' | sudo tee -a /etc/asound.conf",
    ]
  }

  # --- Tailscale client ---
  provisioner "shell" {
    inline = [
      "set -e",
      "curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.noarmor.gpg | sudo tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null",
      "curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/noble.tailscale-keyring.list | sudo tee /etc/apt/sources.list.d/tailscale.list",
      "sudo apt-get update",
      "sudo apt-get install -y tailscale",
    ]
  }

  # --- uv (package manager) ---
  provisioner "shell" {
    inline = [
      "curl -LsSf https://astral.sh/uv/install.sh | sh",
      "echo 'export PATH=\"$HOME/.local/bin:$PATH\"' >> ~/.bashrc",
    ]
  }

  # --- Game files ---
  provisioner "shell" {
    inline = [
      "sudo mkdir -p /opt/starsector",
      "sudo chown ubuntu:ubuntu /opt/starsector",
    ]
  }
  provisioner "file" {
    source      = "${var.game_dir}/"
    destination = "/opt/starsector/"
  }

  # --- Project source + venv ---
  provisioner "shell" {
    inline = [
      "sudo mkdir -p /opt/starsector-optimizer/src",
      "sudo chown -R ubuntu:ubuntu /opt/starsector-optimizer",
    ]
  }
  provisioner "file" {
    source      = "${var.project_src}/"
    destination = "/opt/starsector-optimizer/src/"
  }
  provisioner "file" {
    source      = "./pyproject.toml"
    destination = "/opt/starsector-optimizer/pyproject.toml"
  }
  provisioner "file" {
    source      = "./uv.lock"
    destination = "/opt/starsector-optimizer/uv.lock"
  }
  provisioner "shell" {
    inline = [
      "cd /opt/starsector-optimizer && /home/ubuntu/.local/bin/uv sync --frozen",
    ]
  }

  # --- Game activation (prefs.xml) ---
  provisioner "shell" {
    inline = [
      "mkdir -p /home/ubuntu/.java/.userPrefs/com/fs/starfarer",
    ]
  }
  provisioner "file" {
    source      = "./scripts/cloud/packer/prefs.xml"
    destination = "/home/ubuntu/.java/.userPrefs/com/fs/starfarer/prefs.xml"
  }

  # --- Systemd unit for worker_agent ---
  provisioner "file" {
    source      = "./scripts/cloud/packer/starsector-worker.service"
    destination = "/tmp/starsector-worker.service"
  }
  provisioner "shell" {
    inline = [
      "sudo mv /tmp/starsector-worker.service /etc/systemd/system/starsector-worker.service",
      "sudo chown root:root /etc/systemd/system/starsector-worker.service",
      "sudo systemctl daemon-reload",
      "sudo systemctl enable starsector-worker.service",
    ]
  }

  # --- Post-build validation (AMI is only tagged on zero exit code) ---
  provisioner "shell" {
    inline = [
      "set -e",
      "# Validate XRandR warmup path works under Xvfb.",
      "which xrandr",
      "Xvfb :99 -screen 0 1920x1080x24 -nolisten tcp &",
      "XVFB_PID=$!",
      "sleep 1",
      "DISPLAY=:99 xrandr --query > /dev/null",
      "kill $XVFB_PID || true",
      "# Validate Python worker_agent imports cleanly.",
      "cd /opt/starsector-optimizer && /home/ubuntu/.local/bin/uv run python -c 'from starsector_optimizer.worker_agent import main; print(\"OK\")'",
    ]
  }
}
