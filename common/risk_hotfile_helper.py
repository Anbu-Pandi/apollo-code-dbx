'''
Created on Jun 11, 2018

@author: yma
'''

def getEMRiskHotFileFilter():
    return [          
        {
            'Sleeve':   'ATHENE',
            'Entity':   'ATHENESUB',
            'Fund':     None,
            'Strategy': ['EM Corporates', 'EM New', 'EM Athene Held To Maturity', 'Emerging Markets Legacy'],
            'DB_Name':  'ATHENE'
            },          
        {
            'Sleeve':   'TRF',
            'Entity':   None,
            'Fund':     ['TREMCH', 'TREMCLS', 'TREMCCAN'],
            'Strategy': None,
            'DB_Name':  'TRF'
            },    
        {
            'Sleeve':   'IVYMULT',
            'Entity':   'IVYMAI',
            'Fund':     None,
            'Strategy': ['EM Corporates', 'EM New'],
            'DB_Name':  'IVYMULT'
            },   
        {
            'Sleeve':   'IVYSTRAT',
            'Entity':   'IVYSTRAT',
            'Fund':     None,
            'Strategy': ['EM Corporates', 'EM New'],
            'DB_Name':  'IVYSTRAT'
            },   
        {
            'Sleeve':   'ACE',
            'Entity':   None,
            'Fund':     ['ACEEM'],
            'Strategy': None,
            'DB_Name':  'ACEEM'
            },   
        {
            'Sleeve':   'CADB',
            'Entity':   'CADBMA',
            'Fund':     None,
            'Strategy': ['EM Corporates'],
            'DB_Name':  'CADB'
            },  
        {
            'Sleeve':   'MPI',
            'Entity':   'MPISMA',
            'Fund':     None,
            'Strategy': ['EM Corporates'],
            'DB_Name':  'MPI'
            },
        {
            'Sleeve':   'ACEEMN',
            'Entity':   'ACEUS',
            'Fund':     ['ACFEM'],
            'Strategy': None,
            'DB_Name':  'ACEEMN'
            },                                                                               
        ]
    
    
    
    
    
    
    
    
    
    
    
    