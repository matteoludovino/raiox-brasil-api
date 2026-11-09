"""Real source payload formats, with fictitious data where applicable."""

CEP = "01310100"
CNPJ = "11222333000181"
IBGE_SP = "3550308"

BRASILAPI_CEP = {
    "cep": CEP,
    "state": "SP",
    "city": "São Paulo",
    "neighborhood": "Bela Vista",
    "street": "Avenida Paulista",
    "service": "open-cep",
    "location": {"type": "Point", "coordinates": {"longitude": "-46.6544", "latitude": "-23.5629"}},
}
VIACEP = {
    "cep": "01310-100",
    "logradouro": "Avenida Paulista",
    "complemento": "de 612 a 1510 - lado par",
    "unidade": "",
    "bairro": "Bela Vista",
    "localidade": "São Paulo",
    "uf": "SP",
    "estado": "São Paulo",
    "regiao": "Sudeste",
    "ibge": IBGE_SP,
    "gia": "1004",
    "ddd": "11",
    "siafi": "7107",
}
OPENCEP = {
    "cep": "01310-100",
    "logradouro": "Avenida Paulista",
    "complemento": "de 612 a 1510 - lado par",
    "bairro": "Bela Vista",
    "localidade": "São Paulo",
    "uf": "SP",
    "ibge": IBGE_SP,
}
_UF_SP = {
    "id": 35,
    "sigla": "SP",
    "nome": "São Paulo",
    "regiao": {"id": 3, "sigla": "SE", "nome": "Sudeste"},
}
IBGE_MUNICIPIO = {
    "id": 3550308,
    "nome": "São Paulo",
    "microrregiao": {
        "id": 35061,
        "nome": "São Paulo",
        "mesorregiao": {"id": 3515, "nome": "Metropolitana de São Paulo", "UF": _UF_SP},
    },
    "regiao-imediata": {
        "id": 350001,
        "nome": "São Paulo",
        "regiao-intermediaria": {"id": 3501, "nome": "São Paulo", "UF": _UF_SP},
    },
}

BRASILAPI_CNPJ = {
    "cnpj": CNPJ,
    "razao_social": "EMPRESA EXEMPLO LTDA",
    "nome_fantasia": "EXEMPLO",
    "descricao_situacao_cadastral": "ATIVA",
    "data_situacao_cadastral": "2015-03-10",
    "data_inicio_atividade": "2010-01-05",
    "natureza_juridica": "Sociedade Empresária Limitada",
    "porte": "DEMAIS",
    "capital_social": 150000,
    "cnae_fiscal": 6201501,
    "cnae_fiscal_descricao": "Desenvolvimento de programas de computador sob encomenda",
    "cnaes_secundarios": [
        {"codigo": 6204000, "descricao": "Consultoria em tecnologia da informação"},
        {"codigo": 0, "descricao": ""},
    ],
    "qsa": [
        {
            "nome_socio": "FULANA DE TAL",
            "qualificacao_socio": "Sócio-Administrador",
            "data_entrada_sociedade": "2010-01-05",
        }
    ],
    "descricao_tipo_de_logradouro": "AVENIDA",
    "logradouro": "PAULISTA",
    "numero": "1000",
    "complemento": "ANDAR 10",
    "bairro": "BELA VISTA",
    "municipio": "SAO PAULO",
    "uf": "SP",
    "cep": CEP,
    "ddd_telefone_1": "1133334444",
    "email": None,
}
CNPJWS = {
    "razao_social": "EMPRESA EXEMPLO LTDA",
    "capital_social": "150000.00",
    "porte": {"id": "05", "descricao": "Demais"},
    "natureza_juridica": {"id": "2062", "descricao": "Sociedade Empresária Limitada"},
    "socios": [
        {
            "nome": "FULANA DE TAL",
            "qualificacao_socio": {"id": 49, "descricao": "Sócio-Administrador"},
            "data_entrada": "2010-01-05",
        }
    ],
    "estabelecimento": {
        "cnpj": CNPJ,
        "nome_fantasia": "EXEMPLO",
        "situacao_cadastral": "Ativa",
        "data_situacao_cadastral": "2015-03-10",
        "data_inicio_atividade": "2010-01-05",
        "tipo_logradouro": "Avenida",
        "logradouro": "Paulista",
        "numero": "1000",
        "complemento": "Andar 10",
        "bairro": "Bela Vista",
        "cep": CEP,
        "ddd1": "11",
        "telefone1": "33334444",
        "email": "contato@exemplo.com.br",
        "atividade_principal": {
            "id": "6201501",
            "descricao": "Desenvolvimento de programas de computador sob encomenda",
        },
        "atividades_secundarias": [
            {"id": "6204000", "descricao": "Consultoria em tecnologia da informação"}
        ],
        "estado": {"sigla": "SP"},
        "cidade": {"nome": "São Paulo", "ibge_id": 3550308},
    },
}
RECEITAWS = {
    "status": "OK",
    "cnpj": "11.222.333/0001-81",
    "nome": "EMPRESA EXEMPLO LTDA",
    "fantasia": "EXEMPLO",
    "abertura": "05/01/2010",
    "situacao": "ATIVA",
    "data_situacao": "10/03/2015",
    "natureza_juridica": "206-2 - Sociedade Empresária Limitada",
    "porte": "DEMAIS",
    "capital_social": "150000.00",
    "atividade_principal": [
        {"code": "62.01-5-01", "text": "Desenvolvimento de programas de computador sob encomenda"}
    ],
    "atividades_secundarias": [
        {"code": "62.04-0-00", "text": "Consultoria em tecnologia da informação"}
    ],
    "qsa": [{"nome": "FULANA DE TAL", "qual": "49-Sócio-Administrador"}],
    "logradouro": "AV PAULISTA",
    "numero": "1000",
    "complemento": "ANDAR 10",
    "bairro": "BELA VISTA",
    "municipio": "SAO PAULO",
    "uf": "SP",
    "cep": "01.310-100",
    "email": "",
    "telefone": "(11) 3333-4444",
}
