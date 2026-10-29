"""Relocation Camper Watch: watches Imoova and Transfercar for motorhome relocations in Australia (brief 2026-10-29).

Polls hourly 6am-10pm Hobart time, keeps every listing in Postgres (data is never deleted), shows matches on a
three-page dashboard and sends one Signal digest a day at 4 pm. Booking stays manual.
"""
