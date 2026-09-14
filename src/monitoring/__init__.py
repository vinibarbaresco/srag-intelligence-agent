"""Monitoramento entre execucoes: regras de alerta e historico de relatorios.

Transforma o relatorio pontual em acompanhamento: cada execucao e comparada
com limiares declarados na configuracao e com a execucao anterior do mesmo
recorte. O resultado e deterministico e entra no relatorio como DADO.
"""
