@echo off
echo Removendo lock do git...
if exist .git\index.lock del /f .git\index.lock
echo Configurando git...
git config user.email "caua.fer@gmail.com"
git config user.name "Caua Ferreira"
echo Fazendo commit de tudo e push...
git add .
git commit -m "release: v1.1.1 - atualiza README multilíngue e corrige publish.yml"
git push
echo Criando e publicando tag v1.1.1...
git tag v1.1.1
git push origin v1.1.1
echo.
echo Pronto! v1.1.1 publicada. Pode fechar essa janela.
pause
