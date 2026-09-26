# Homebrew cask 模板：WorldQuant 菜单栏 App（DMG）
#
# 发布流程：
#   1. tag 触发的 dmg.yml 会把 WorldQuant-<ver>.dmg 挂到 GitHub Release
#   2. 把本文件复制到 tap 的 Casks/worldquant.rb，填入 DMG 的真实 sha256：
#        shasum -a 256 dist/WorldQuant-<ver>.dmg
#   3. 用户安装：brew install --cask Octo-o-o-o/autowq/worldquant
#      （brew 安装 cask 会去除 quarantine，免 Apple 公证）

cask "worldquant" do
  version "0.2.0"
  sha256 "UPDATE_ON_RELEASE"

  url "https://github.com/Octo-o-o-o/autowq/releases/download/v#{version}/WorldQuant-#{version}.dmg"
  name "WorldQuant"
  desc "Menu-bar companion for local WorldQuant BRAIN research orchestration"
  homepage "https://github.com/Octo-o-o-o/autowq"

  depends_on macos: :monterey

  app "WorldQuant.app"

  zap trash: [
    "~/Library/Application Support/WorldQuant",
    "~/Library/LaunchAgents/com.worldquant.wq-menu.plist",
    "~/Library/LaunchAgents/com.worldquant.wq-runner.plist",
  ]
end
