@echo off
echo Removendo lock do git...
if exist .git\index.lock del /f .git\index.lock
echo Configurando git...
git config user.email "caua.fer@gmail.com"
git config user.name "Caua Ferreira"
echo Fazendo commit e push...
git add pyproject.toml .github\workflows\publish.yml
git commit -m "fix: corrige versao para 1.0.2 e adiciona workflow_dispatch no publish"
git push
echo.
echo Pronto! Pode fechar essa janela.
pause
