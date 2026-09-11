MES Git Sync - Fixed Encoding Version
======================================

This version uses ASCII-only .bat files with no UTF-8 BOM.
It avoids Windows CMD errors such as:
'癤?echo' is not recognized...

Files:
- .gitignore
- git_first_setup_MES.bat
- git_push.bat
- git_pull.bat

Usage:
1. Extract these files into:
   C:\문서\공유폴더\BarcodeApp

2. Run:
   git_first_setup_MES.bat

3. Later:
   Before work  -> git_pull.bat
   After work   -> git_push.bat
