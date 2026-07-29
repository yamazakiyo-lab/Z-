@echo off
rem ============================================================
rem  【転送板】GDX→MIE改名(2026-07-29)の互換用。
rem  スケジュールタスク(MIE_DailyRun)が旧パスを指していても動くよう、
rem  新しい run_mie_wrapper.bat へそのまま引き継ぐ。
rem  タスクの参照先を run_mie_wrapper.bat に変更したら削除してよい。
rem ============================================================
call "%~dp0run_mie_wrapper.bat" %*
exit /b %ERRORLEVEL%
