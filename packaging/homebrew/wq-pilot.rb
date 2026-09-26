# Homebrew formula 模板：wq-pilot
#
# 发布流程：
#   1. 维护者创建 tap 仓库 github.com/Octo-o-o-o/homebrew-autowq
#   2. 每次发 tag 后，把本文件复制到 tap 的 Formula/wq-pilot.rb，
#      替换 url 为 PyPI sdist 地址并填入真实 sha256：
#        pip download wq-pilot==<ver> --no-deps --no-binary :all: -d /tmp
#        shasum -a 256 /tmp/wq_pilot-<ver>.tar.gz
#   3. 用户安装：brew tap Octo-o-o-o/autowq && brew install wq-pilot
#
# 引擎零第三方依赖，virtualenv_install_with_resources 直接可用。

class WqPilot < Formula
  include Language::Python::Virtualenv

  desc "Local WorldQuant BRAIN research orchestration with gated providers"
  homepage "https://github.com/Octo-o-o-o/autowq"
  url "https://files.pythonhosted.org/packages/source/w/wq-pilot/wq_pilot-0.2.0.tar.gz"
  sha256 "UPDATE_ON_RELEASE"
  license "Apache-2.0"

  depends_on "python@3.13"

  def install
    virtualenv_install_with_resources
  end

  def caveats
    <<~EOS
      在任意空目录初始化工作区：
        mkdir ~/autowq && cd ~/autowq
        wq onboard
      macOS 桌面菜单栏见 cask：brew install --cask Octo-o-o-o/autowq/worldquant
    EOS
  end

  test do
    assert_match version.to_s, shell_output("#{bin}/wq --version")
  end
end
