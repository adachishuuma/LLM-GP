<#
.SYNOPSIS
  Fully shut down WSL, then run a command inside it. Run this from Windows
  PowerShell (not from inside a WSL bash shell -- `wsl --shutdown` would kill
  the very session you're typing in).

.DESCRIPTION
  Clears any accumulated WSL/Gazebo state (stray processes, driver state,
  etc.) before starting a fresh ROS/Gazebo evaluation or GP run. `wsl
  --shutdown` closes every WSL session and distro, not just one terminal, so
  close other WSL windows first if you don't want them to disappear too.

.PARAMETER Command
  The bash command line to run inside WSL after it restarts. Quote it as one
  string; use && to chain steps.

.PARAMETER Distro
  WSL distribution name. Defaults to Ubuntu-20.04 (matches
  config/*.yaml's wsl_distribution).

.PARAMETER WaitSeconds
  Seconds to pause after `wsl --shutdown` before relaunching, so the VM has
  time to fully release resources. Default 5.

.EXAMPLE
  # Quick 3-repetition sanity check
  .\scripts\restart_wsl_and_run.ps1 -Command "cd /mnt/c/Users/adachi/catkin_ws && python3 scripts/verify_generation_gain.py run_20260811_065434 --repetitions 3"

.EXAMPLE
  # Full 10-generation GP run, backgrounded so it survives closing the window
  .\scripts\restart_wsl_and_run.ps1 -Command "cd /mnt/c/Users/adachi/catkin_ws && nohup python3 -m llm_gp.main --config config/ros_gazebo_lattice_fork_10gen.yaml > gp_10gen.log 2>&1 & disown"
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Command,

    [string]$Distro = "Ubuntu-20.04",

    [int]$WaitSeconds = 5
)

Write-Host "Shutting down WSL (all distros/sessions)..." -ForegroundColor Yellow
wsl --shutdown

Write-Host "Waiting $WaitSeconds seconds for WSL to fully release resources..." -ForegroundColor Yellow
Start-Sleep -Seconds $WaitSeconds

Write-Host "Running in WSL ($Distro):" -ForegroundColor Yellow
Write-Host "  $Command"
wsl -d $Distro -- bash -lc $Command
