import re


def get_swaption_name(currency, underlying_rate_tenor, option_direction, option_tenor, fwd_start_tenor, swap_tenor,
                      strike, settlement):
    # example: Swaption EUR-3m Payer 1m 1y ATM Physically Settled
    underlying_rate = f"{currency.upper()}-{underlying_rate_tenor.lower()}"
    fwd_start_tenor = '' \
        if (fwd_start_tenor is None or int(re.sub('[a-zA-Z]', '', fwd_start_tenor)) == 0) \
        else (fwd_start_tenor + ' ')
    return f"Swaption {underlying_rate} {option_direction.title()} {option_tenor.lower()} {fwd_start_tenor.lower()}{swap_tenor.lower()} {strike.upper()} {settlement.title()}"


def get_swaption_detail(curve_name):
    details = curve_name.split(' ')
    assert (len(details) == 8) or (len(details) == 9), f"incorrect swaption name: {curve_name}"

    option_type = details[0].title()
    currency = details[1].split('-')[0].upper()
    underlying_rate_tenor = details[1].split('-')[1].lower()
    option_direction = details[2].title()
    option_tenor = details[3].lower()
    i = 0
    fwd_start_tenor = '0b'
    if len(details) == 9:
        i = 1
        fwd_start_tenor = details[4].lower()
    swap_tenor = details[4 + i].lower()
    strike = details[5 + i].upper()
    settlement = details[6 + i].title() + ' ' + details[7 + i].title()

    return (
    option_type, currency, underlying_rate_tenor, option_direction, option_tenor, fwd_start_tenor, swap_tenor, strike,
    settlement)
