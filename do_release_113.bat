@echo off
echo Removendo lock do git...
if exist .git\index.lock del /f .git\index.lock
echo Configurando git...
git config user.email "caua.fer@gmail.com"
git config user.name "Caua Ferreira"
echo Fazendo commit e push...
git add .
git commit -m "fix: corrige links de navegacao do README e adiciona CONTRIBUTING.md"
git push
echo Criando e publicando tag v1.1.3...
git tag v1.1.3
git push origin v1.1.3
echo.
echo Pronto! v1.1.3 publicada. Pode fechar essa janela.
pause
