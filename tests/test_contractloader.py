#!/usr/bin/env python
import os
from load_contracts import ContractLoader

SRC = os.path.join(os.path.dirname(os.path.realpath(__file__)), os.pardir, 'src')
SAVENAME = 'test_contractloader_save.json'
c = ContractLoader()
c.load_from_source(SRC, 'controller.se', ['mutex.se', 'cash.se', 'repContract.se'])
c.save(SAVENAME)

new_c = ContractLoader()
new_c.load_from_save(SAVENAME)
