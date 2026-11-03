@echo off
rem AINS platform control: start / stop / restart / status / logs / build / portal / doctor / verify
rem Runs Docker Compose in ains-orchestrator. Needs Docker Desktop running and a filled .env.
setlocal
set "HERE=%~dp0"
set "ORCH=%HERE%ains-orchestrator"
set "PORTAL_URL=http://localhost:8787"
if not exist "%ORCH%\docker-compose.yml" (
  echo ains-orchestrator\docker-compose.yml not found next to this script.
  exit /b 2
)
cd /d "%ORCH%"
if "%~1"=="" goto help

if /i "%~1"=="start"   ( docker compose up -d & echo portal: %PORTAL_URL% & goto done )
if /i "%~1"=="stop"    ( docker compose stop & goto done )
if /i "%~1"=="restart" ( docker compose restart & goto done )
if /i "%~1"=="build"   ( docker compose up -d --build & echo portal: %PORTAL_URL% & goto done )
if /i "%~1"=="down"    ( docker compose down & goto done )
if /i "%~1"=="logs"    ( docker compose logs -t -f --tail 200 & goto done )
if /i "%~1"=="doctor"  ( docker compose exec orchestrator ains doctor & goto done )
if /i "%~1"=="verify"  (
  if "%~2"=="" ( echo usage: %~n0 verify ^<repo-name^> ^<JIRA-KEY^> & exit /b 2 )
  uv run ains target verify %2 %3 & goto done
)
if /i "%~1"=="portal"  ( start "" "%PORTAL_URL%" & goto done )
if /i "%~1"=="backup"  (
  set "STAMP=%date:~-4%%date:~3,2%%date:~0,2%-%time:~0,2%%time:~3,2%"
  set "STAMP=%STAMP: =0%"
  if not exist "%HERE%backup" mkdir "%HERE%backup"
  docker cp ains-orchestrator:/data "%HERE%backup\data-%STAMP%" & echo backup written to %HERE%backup\data-%STAMP% & goto done
)
if /i "%~1"=="status"  (
  echo == containers
  docker compose ps
  echo.
  echo == stories and what a person must do next
  docker compose exec -T orchestrator ains status
  echo.
  echo == portal: %PORTAL_URL%
  goto done
)
echo unknown command: %~1
:help
echo usage: %~n0 ^<command^>
echo   start     start the orchestrator and the portal (docker compose up -d)
echo   stop      stop both containers; state is kept
echo   restart   restart both containers
echo   build     rebuild the image after a platform code change, then start
echo   down      stop and remove the containers (the work volume is kept)
echo   status    container health, every story and its next human action, portal link
echo   logs      follow the orchestrator log with timestamps (Ctrl+C to stop following)
echo   doctor    check tokens, tools and connections inside the container
echo   verify    verify a project's onboarding: %~n0 verify ains-sandbox AINSSB
echo   portal    open the management portal in the browser (%PORTAL_URL%)
echo   backup    copy the data volume (sessions.sqlite, heartbeat, logs) to backup\data-^<stamp^>
exit /b 1
:done
endlocal
