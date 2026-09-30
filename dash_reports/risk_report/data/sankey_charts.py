# -*- coding: utf-8 -*-
"""
YM: Sankey chart by strategy for total pnl (ex FX pnl col)

"""
import logging
from collections import defaultdict

import pandas as pd
from colour import Color
from plotly.offline import init_notebook_mode

# Packages
from dash_reports.report_common.data.data_model import datastore

init_notebook_mode(connected=True)
import cufflinks as cf

init_notebook_mode(connected=True)
cf.go_offline()


# Create folder to output PDFs into - remember to modify in plotSankey function
# if not os.path.exists('issuers2strategies'):
#     os.mkdir('issuers2strategies')

def analyzeDates(dateList):
    minDate = dateList[0]
    maxDate = dateList[len(dateList) - 1]
    return minDate, maxDate


# Appends Colors and Link Colors for First Tier Nodes
def stratColors(color, node, colorsList, linkColors, switch):
    # Make link Colors to Initial Node
    init_linkcolor = ''
    for y in range(0, len(color) - 2):
        init_linkcolor = init_linkcolor + color[y]
    init_linkcolor = init_linkcolor + '0.5)'
    for x in range(0, len(node) - 1):
        linkColors.append(init_linkcolor)
    # Create Color Range for First Tier Nodes
    gradient = []
    if switch == 'p':
        start_color = Color("#73d56c")
        grad = list(start_color.range_to(Color("#ff6a38"), 20))
        gradient = [c.hex for c in grad]
    if switch == 'n':
        start_color = Color('#992622')
        grad = list(start_color.range_to(Color('#ffe4e1'), 20))
        gradient = [c.hex for c in grad]
        colorsList.append(gradient[len(node)])
    for x in range(0, len(node)):
        colorsList.append(gradient[x])


# Turns a hexadecimal color value to RGBA
def hexToRGB(hexa, alpha):
    # red, green, blue = bytes.fromhex(hexa[1:])
    rgb = list(tuple(int(hexa[1:][i:i + 2], 16) for i in (0, 2, 4)))  # bytes.fromhex(hexa[1:])
    red = rgb[0]
    green = rgb[1]
    blue = rgb[2]
    return "rgba(" + str(red) + ", " + str(green) + ", " + str(blue) + ", " + str(alpha) + ")"


# Creates colors for issuer links
def issueLinks(start, colors, link_colors, x, ncolor):
    # Make Link Colors
    index = start[x]
    color = colors[index]
    new_linkcolor = hexToRGB(color, 0.5)
    link_colors.append(new_linkcolor)
    colors.append(ncolor)


def findTop(top, alphaMap, newMap):
    for _ in range(0, top):
        highest = 0
        name = ""
        for key, value in list(alphaMap.items()):
            if (abs(value) > abs(highest)):
                highest = abs(value)
                name = key
        newMap[name] = highest
        del alphaMap[name]

    # Group Other and Add
    other_value = 0
    name = "Other"
    for key, value in list(alphaMap.items()):
        other_value += abs(value)
    newMap[name] = other_value
    return newMap


def formatNumber(value):
    value = round(value / 1000)
    value = "{0:,.2f}".format(value)
    newValue = ''
    for i in range(0, len(value) - 3):
        newValue += value[i]
    return newValue


def plotSankey(start, end, node, amount, link_colors, colors, title, FUND):
    data_trace = dict(
        type='sankey',
        domain=dict(
            x=[0, 1],
            y=[0, 1]
        ),
        orientation="h",
        valueformat=".0f",
        node=dict(
            pad=10,
            thickness=30,
            line=dict(
                width=0.5
            ),
            label=node,
            color=colors,
        ),
        link=dict(
            source=start,
            target=end,
            value=amount,
            color=link_colors,
        )
    )
    layout = dict(
        title=title,
        height=700,  # 700,
        width=800,  # 900,
        font=dict(
            size=15
        ),
    )
    fig = dict(data=[data_trace], layout=layout)

    return fig


def generate_tot_pnl_sankey(fundList):
    df = datastore.get_pnl()

    FX = ['Cash', 'FX Offset', 'FX Forward', 'FX Option', 'Currency Swap', 'Interest Rate Option']
    df.loc[df['Security Type'].isin(FX), 'FO Strategy Broad Category'] = 'Rate & FX'
    df.loc[df['Security Type'].isin(FX), 'Issuer Ult'] = 'Rate & FX'

    pTable = pd.pivot_table(df, index=["Fund Entity Code", 'FO Strategy Broad Category', 'Issuer Ult'],
                            values=['P&L DTD Total x FX USD'], aggfunc=sum)

    fundMap = defaultdict(list)
    strategyMap = defaultdict(list)

    fundList.sort()
    result = {}
    '''
    Create strategy to issuer mapping
    '''
    fIdx = 1
    for fund in fundList:
        logging.info(('Run for {0}:    {1}/{2}'.format(fund, fIdx, len(fundList))))

        dfForFund = df[df['Fund Entity Code'] == fund]
        fIdx = fIdx + 1

        stratList = list(df['FO Strategy Broad Category'].unique())
        strategyMap = defaultdict(list)

        for strat in stratList:
            stratDf = dfForFund[dfForFund['FO Strategy Broad Category'] == strat]
            strategyMap[strat] = list(stratDf['Issuer Ult'].unique())

        dateList = list(df['As Of Date'].unique())
        dateList.sort()
        minDate = min(dateList)
        maxDate = max(dateList)

        # Keep track of strategy sum
        posStrategySum = dict()
        negStrategySum = dict()
        strategySum = dict()
        for strategy, issuer in list(strategyMap.items()):
            for x in range(0, len(issuer)):
                num = int(pTable.loc[(fund, strategy, issuer[x])])
                if strategy not in strategySum:
                    strategySum[strategy] = num
                else:
                    strategySum[strategy] += num

        for key, value in list(strategySum.items()):
            if value > 0:
                posStrategySum[key] = value
            if value < 0:
                negStrategySum[key] = value

        posMap = dict()
        negMap = dict()
        top = 10
        if len(posStrategySum) > top:
            findTop(top, posStrategySum, posMap)
        else:
            posMap = posStrategySum
        if len(negStrategySum) > top:
            findTop(top, negStrategySum, negMap)
        else:
            negMap = negStrategySum

        # Create First Tier Nodes and Amounts
        pos_sum = 0
        neg_sum = 0
        pos_node = []
        pos_strategyList = [fund]
        neg_node = []
        neg_strategyList = [fund]
        pos_amount = []
        neg_amount = []
        for key, value in list(posMap.items()):
            pos_node.append(key + ": " + str(formatNumber(value)))
            pos_strategyList.append(key)
            pos_amount.append(value)
            pos_sum += value
        for key, value in list(negMap.items()):
            neg_node.append(key + ": " + str(formatNumber(value)))
            neg_strategyList.append(key)
            neg_amount.append(abs(value))
            neg_sum += value

        # Insert Center Node
        pos_node.insert(0, fund + ": (+) = " + str(formatNumber(pos_sum)) + " (-) = " + str(formatNumber(neg_sum)))
        neg_node.insert(0,
                        fund + ": (+) = " + str(formatNumber(pos_sum)) + " (-) = -" + str(formatNumber(abs(neg_sum))))

        # Make Start and End
        pos_start = []
        neg_start = []
        pos_end = []
        neg_end = []
        for x in range(0, len(posMap)):
            pos_start.append(0)
            pos_end.append(x + 1)
        for x in range(0, len(negMap)):
            neg_start.append(0)
            neg_end.append(x + 1)

        # Colors
        pos_colors = []
        neg_colors = []
        pos_link_colors = []
        neg_link_colors = []
        stratColors('rgba(127, 194, 65, 1)', pos_node, pos_colors, pos_link_colors, 'p')
        stratColors('rgba(219, 10, 10, 1)', neg_node, neg_colors, neg_link_colors, 'n')

        # Create Second Tier
        pStartCol = Color("#94e17f")
        pGrad = list(pStartCol.range_to(Color("#d9ffb3"), 100))
        pGradient = [c.hex for c in pGrad]
        nStartCol = Color("#ff6d38")
        nGrad = list(nStartCol.range_to(Color("#d26c6c"), 100))
        nGradient = [c.hex for c in nGrad]

        for strategy, issuer in list(strategyMap.items()):
            pos_issueMap = dict()
            neg_issueMap = dict()
            issueMap = dict()
            for x in range(0, len(issuer)):
                num = int(pTable.loc[(fund, strategy, issuer[x])])
                if issuer[x] not in issueMap:
                    issueMap[issuer[x]] = num
            for key, value in list(issueMap.items()):
                if value > 0:
                    pos_issueMap[key] = value
                if value < 0:
                    neg_issueMap[key] = value
            top = 3
            if strategy in pos_strategyList:
                top_posIssuers = dict()
                if len(pos_issueMap) > top:
                    findTop(top, pos_issueMap, top_posIssuers)
                else:
                    top_posIssuers = pos_issueMap
                for key, value in list(top_posIssuers.items()):
                    pos_start.append(pos_strategyList.index(strategy))
                    pos_end.append(max(pos_end) + 1)
                    pos_amount.append(value)
                    pos_node.append(key + ": " + str(formatNumber(value)))
                    pos_colors.append(pGradient[max(pos_end) + 1])
                    pos_link_colors.append(hexToRGB(pos_colors[pos_strategyList.index(strategy)], 0.5))
            if strategy in neg_strategyList:
                top_negIssuers = dict()
                if len(neg_issueMap) > top:
                    findTop(top, neg_issueMap, top_negIssuers)
                else:
                    top_negIssuers = neg_issueMap
                for key, value in list(top_negIssuers.items()):
                    neg_start.append(neg_strategyList.index(strategy))
                    neg_end.append(max(neg_end) + 1)
                    neg_amount.append(abs(value))
                    neg_node.append(key + ": " + str(formatNumber(-1 * abs(value))))
                    neg_colors.append(nGradient[max(neg_end) + 1])
                    neg_link_colors.append(hexToRGB(neg_colors[neg_strategyList.index(strategy)], 0.5))

        # Green for Positive, Red for Negative
        init_colors = ['rgba(127, 194, 65, 1)', 'rgba(219, 10, 10, 1)']

        title = fund + " Total PnL: $" + str(formatNumber(pos_sum + neg_sum)) + " from " + str(
            pd.to_datetime(minDate).date()) + " to " + str(pd.to_datetime(maxDate).date())

        node = neg_node
        for x in range(1, len(pos_node)):
            node.append(pos_node[x])

        start = neg_end
        length = 0
        if len(neg_end) > 0:
            length = max(neg_end)
        for i in range(0, len(pos_start)):
            if pos_start[i] == 0:
                start.append(0)
            else:
                start.append(length + pos_start[i])

        end = neg_start
        for i in range(0, len(pos_end)):
            end.append(pos_end[i] + length)

        amount = neg_amount + pos_amount
        link_colors = neg_link_colors + pos_link_colors
        colors = []
        if (pos_sum + neg_sum > 0):
            colors = neg_colors[1:] + pos_colors
            colors[0] = 'rgba(127, 194, 65, 1)'
        else:
            colors = neg_colors[1:] + pos_colors[1:]
            colors[0] = 'rgba(219, 10, 10, 1)'

        fig = plotSankey(start, end, node, amount, link_colors, colors, title, fund)
        result[fund] = fig

    return result


def generate_tot_alpha_sankey(fundList):
    df = datastore.get_pnl()

    FX = ['Cash', 'FX Offset', 'FX Forward', 'FX Option', 'Currency Swap', 'Interest Rate Option']
    df.loc[df['Security Type'].isin(FX), 'FO Strategy Broad Category'] = 'Rate & FX'
    df.loc[df['Security Type'].isin(FX), 'Issuer Ult'] = 'Rate & FX'

    pTable = pd.pivot_table(df, index=["Fund Entity Code", 'Issuer Ult'], values=['Rate PnL', 'Alpha PnL'], aggfunc=sum)

    fundList.sort()

    dateList = list(df['As Of Date'].unique())
    dateList.sort()
    minDate = min(dateList)
    maxDate = max(dateList)

    result = {}

    fIdx = 1
    for fund in fundList:
        logging.info(('Run for {0}:    {1}/{2}'.format(fund, fIdx, len(fundList))))

        dfForFund = df[df['Fund Entity Code'] == fund]
        fIdx = fIdx + 1

        issuerList = list(dfForFund['Issuer Ult'].unique())
        pos_issueMap = dict()
        neg_issueMap = dict()
        for issuer in issuerList:
            df2 = pTable.loc[(fund, issuer)]
            column = df2.index

            num = df2[0] + df2[1]
            if num > 0:
                if issuer not in pos_issueMap:
                    pos_issueMap[issuer] = num
                else:
                    pos_issueMap[issuer] += num
            if num < 0:
                if issuer not in neg_issueMap:
                    neg_issueMap[issuer] = num
                else:
                    neg_issueMap[issuer] += num

        posMap = dict()
        negMap = dict()
        top = 10
        if len(pos_issueMap) > top:
            findTop(top, pos_issueMap, posMap)
        else:
            posMap = pos_issueMap
        if len(neg_issueMap) > top:
            findTop(top, neg_issueMap, negMap)
        else:
            negMap = neg_issueMap

        # Create First Tier Nodes and Amounts
        pos_sum = 0
        neg_sum = 0
        pos_node = []
        neg_node = []
        pos_amount = []
        neg_amount = []
        for key, value in list(posMap.items()):
            pos_node.append(key + ": " + str(formatNumber(value)))
            pos_amount.append(value)
            pos_sum += value
        for key, value in list(negMap.items()):
            neg_node.append(key + ": " + str(formatNumber(-1 * abs(value))))
            neg_amount.append(abs(value))
            neg_sum += value

        # Insert Center Node
        pos_node.insert(0, fund + ": (+) = " + str(formatNumber(pos_sum)) + " (-) = " + str(formatNumber(neg_sum)))
        neg_node.insert(0, fund + ": (+) = " + str(formatNumber(pos_sum)) + " (-) = " + str(formatNumber(neg_sum)))

        # Make Start and End
        pos_start = []
        neg_start = []
        pos_end = []
        neg_end = []
        for x in range(0, len(posMap)):
            pos_start.append(0)
            pos_end.append(x + 1)
        for x in range(0, len(negMap)):
            neg_start.append(0)
            neg_end.append(x + 1)

        # Colors
        pos_colors = []
        neg_colors = []
        pos_link_colors = []
        neg_link_colors = []
        stratColors('rgba(127, 194, 65, 1)', pos_node, pos_colors, pos_link_colors, 'p')
        stratColors('rgba(219, 10, 10, 1)', neg_node, neg_colors, neg_link_colors, 'n')

        # Green for Positive, Red for Negative
        init_colors = ['rgba(127, 194, 65, 1)', 'rgba(219, 10, 10, 1)']

        title = fund + " Total Alpha: $" + str(
            formatNumber(pos_sum + (-1 * abs(neg_sum)))) + " from " + str(
            pd.to_datetime(minDate).date()) + " to " + str(pd.to_datetime(maxDate).date())

        # Combine Pos and Neg
        node = neg_node
        for x in range(1, len(pos_node)):
            node.append(pos_node[x])

        start = neg_end
        length = 0
        if len(neg_end) == 0:
            length = 0
        else:
            length = max(neg_end)
        for i in range(0, len(pos_start)):
            if pos_start[i] == 0:
                start.append(0)
            else:
                start.append(length + pos_start[i])

        end = neg_start
        for i in range(0, len(pos_end)):
            end.append(pos_end[i] + length)

        amount = neg_amount + pos_amount
        link_colors = neg_link_colors + pos_link_colors
        colors = []
        if (pos_sum + neg_sum > 0):
            colors = neg_colors[1:] + pos_colors
            colors[0] = 'rgba(127, 194, 65, 1)'
        else:
            colors = neg_colors[1:] + pos_colors
            colors[0] = 'rgba(219, 10, 10, 1)'

        # plot double sided Sankey
        fig = plotSankey(start, end, node, amount, link_colors, colors, title, fund)
        result[fund] = fig

    return result


def generate_issuer_pnl_sankey(fundList):
    df = datastore.get_pnl()

    FX = ['Cash', 'FX Offset', 'FX Forward', 'FX Option', 'Currency Swap', 'Interest Rate Option']
    df.loc[df['Security Type'].isin(FX), 'FO Strategy Broad Category'] = 'Rate & FX'
    df.loc[df['Security Type'].isin(FX), 'Issuer Ult'] = 'Rate & FX'

    pTable = pd.pivot_table(df, index=["Fund Entity Code", 'Issuer Ult'], values=['P&L DTD Total x FX USD'],
                            aggfunc=sum)

    dateList = list(df['As Of Date'].unique())
    dateList.sort()
    minDate = min(dateList)
    maxDate = max(dateList)

    fundList.sort()

    result = {}

    fIdx = 1
    for fund in fundList:
        logging.info(('Run for {0}:    {1}/{2}'.format(fund, fIdx, len(fundList))))

        dfForFund = df[df['Fund Entity Code'] == fund]
        fIdx = fIdx + 1

        issuerList = list(dfForFund['Issuer Ult'].unique())

        pos_issueMap = dict()
        neg_issueMap = dict()

        for issuer in issuerList:
            df2 = pTable.loc[(fund, issuer)]
            column = df2.index
            for y in range(0, len(column)):
                num = df2[0]
                num = int(num)
                if num > 0:
                    if issuer not in pos_issueMap:
                        pos_issueMap[issuer] = num
                    else:
                        pos_issueMap[issuer] += num
                if num < 0:
                    if issuer not in neg_issueMap:
                        neg_issueMap[issuer] = num
                    else:
                        neg_issueMap[issuer] += num

        posMap = dict()
        negMap = dict()
        top = 10
        if len(pos_issueMap) > top:
            findTop(top, pos_issueMap, posMap)
        else:
            posMap = pos_issueMap
        if len(neg_issueMap) > top:
            findTop(top, neg_issueMap, negMap)
        else:
            negMap = neg_issueMap

        # Create First Tier Nodes and Amounts
        pos_sum = 0
        neg_sum = 0
        pos_node = []
        neg_node = []
        pos_amount = []
        neg_amount = []
        for key, value in list(posMap.items()):
            pos_node.append(key + ": " + str(formatNumber(value)))
            pos_amount.append(value)
            pos_sum += value
        for key, value in list(negMap.items()):
            neg_node.append(key + ": " + str(formatNumber(-1 * abs(value))))
            neg_amount.append(abs(value))
            neg_sum += value

        # Insert Center Node
        pos_node.insert(0, fund + ": (+) = " + str(formatNumber(pos_sum)) + " (-) = " + str(formatNumber(neg_sum)))
        neg_node.insert(0, fund + ": (+) = " + str(formatNumber(pos_sum)) + " (-) = " + str(formatNumber(neg_sum)))

        # Make Start and End
        pos_start = []
        neg_start = []
        pos_end = []
        neg_end = []
        for x in range(0, len(posMap)):
            pos_start.append(0)
            pos_end.append(x + 1)
        for x in range(0, len(negMap)):
            neg_start.append(0)
            neg_end.append(x + 1)

        # Colors
        pos_colors = []
        neg_colors = []
        pos_link_colors = []
        neg_link_colors = []
        stratColors('rgba(127, 194, 65, 1)', pos_node, pos_colors, pos_link_colors, 'p')
        stratColors('rgba(219, 10, 10, 1)', neg_node, neg_colors, neg_link_colors, 'n')

        # Green for Positive, Red for Negative
        init_colors = ['rgba(127, 194, 65, 1)', 'rgba(219, 10, 10, 1)']

        title = fund + " Total PnL: $" + str(
            formatNumber(pos_sum + (-1 * abs(neg_sum)))) + " from " + str(
            pd.to_datetime(minDate).date()) + " to " + str(pd.to_datetime(maxDate).date())
        # plotSankey(pos_start, pos_end, pos_node, pos_amount, pos_link_colors, pos_colors, title, fund)
        # time.sleep(1)
        # plotSankey(neg_start, neg_end, neg_node, neg_amount, neg_link_colors, neg_colors, title, fund)

        # Combine Pos and Neg
        node = neg_node
        for x in range(1, len(pos_node)):
            node.append(pos_node[x])

        start = neg_end
        length = 0
        if len(neg_end) == 0:
            length = 0
        else:
            length = max(neg_end)
        for i in range(0, len(pos_start)):
            if pos_start[i] == 0:
                start.append(0)
            else:
                start.append(length + pos_start[i])

        end = neg_start
        for i in range(0, len(pos_end)):
            end.append(pos_end[i] + length)

        amount = neg_amount + pos_amount
        link_colors = neg_link_colors + pos_link_colors
        colors = []
        if (pos_sum + neg_sum > 0):
            colors = neg_colors[1:] + pos_colors
            colors[0] = 'rgba(127, 194, 65, 1)'
        else:
            colors = neg_colors[1:] + pos_colors
            colors[0] = 'rgba(219, 10, 10, 1)'

        fig = plotSankey(start, end, node, amount, link_colors, colors, title, fund)
        result[fund] = fig

    return result


if __name__ == '__main__':
    a = generate_tot_pnl_sankey(['CSTRAT'])
