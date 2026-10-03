cask "worldquant" do
  version "0.2.25"
  sha256 "89793ac295bf7e312a1468c67072190a70492896d02ec78571d4bd203eadeaf1"

  url "https://github.com/Octo-o-o-o/autowq/releases/download/macos-v#{version}/WorldQuant-#{version}.dmg"
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
