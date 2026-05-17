@echo off
echo Removendo lock do git...
if exist .git\index.lock del /f .git\index.lock
echo Configurando git...
git config user.email "caua.fer@gmail.com"
git config user.name "Caua Ferreira"
echo Fazendo commit e push...
git add .github\workflows\docker.yml
git commit -m "fix: corrige namespace do Docker Hub no workflow de build"
git push
echo.
echo Pronto! Pode fechar essa janela.
pause
