#!/usr/bin/env python3
"""
Generate architecture diagram for SCM and Platform layers
"""

from graphviz import Digraph


def create_architecture_diagram():
    """Create a comprehensive architecture diagram"""
    dot = Digraph(comment='SCM and Platform Architecture')
    dot.attr(rankdir='TB', splines='ortho', nodesep='0.5', ranksep='0.8')
    dot.attr('node', shape='box', style='rounded,filled', fontname='Arial', fontsize='10')
    dot.attr('edge', fontname='Arial', fontsize='9')

    # Define color scheme
    base_color = '#E8F4F8'
    impl_color = '#B8E6F0'
    bot_color = '#FFE6CC'
    # interface_color = '#E8F8E8'  # not used yet

    # SCM Layer - Base Classes
    with dot.subgraph(name='cluster_scm') as scm:
        scm.attr(label='SCM Layer (Source Code Management)', style='filled', color='#D0E8F0', fontsize='12', fontname='Arial Bold')

        # Base class
        scm.node('SCMBase', 'SCMBase\n(Abstract)\n\n• checkout_package()',
                 fillcolor=base_color, shape='box', style='rounded,filled,bold')

        # Implementations
        scm.node('SCM_OSC', 'scm.OSC\n\nOBS API', fillcolor=impl_color)
        scm.node('SCM_Git', 'scm.Git\n\nGit Repositories', fillcolor=impl_color)

    # Platform Layer - Base Classes
    with dot.subgraph(name='cluster_platform') as plat:
        plat.attr(label='Platform Layer (Collaboration & Reviews)', style='filled', color='#D0F0D0', fontsize='12', fontname='Arial Bold')

        # Base class
        plat.node(
            'PlatformBase',
            ('PlatformBase\n(Abstract)\n\n'
             '• name: str\n• get_path()\n'
             '• get_request()\n'
             '• get_project_config()\n'
             '• get_request_age()\n'
             '• get_request_list_with_history()\n'
             '• get_staging_api()\n'
             '• search_review()\n'
             '• can_accept_review()\n'
             '• change_review_state()'),
            fillcolor=base_color,
            shape='box',
            style='rounded,filled,bold',
            width='3'
        )

        # Implementations
        plat.node('Plat_OBS', 'plat.OBS\n\nOBS API', fillcolor=impl_color)
        plat.node('Plat_Gitea', 'plat.Gitea\n\nGitea REST API', fillcolor=impl_color)

    # Bots Layer
    with dot.subgraph(name='cluster_bots') as bots:
        bots.attr(label='Bots (Business Logic)', style='filled', color='#F0E8D0', fontsize='12', fontname='Arial Bold')

        bots.node('ReviewBot', 'ReviewBot\n(Base)', fillcolor=bot_color, shape='box', style='rounded,filled')
        bots.node('check_source', 'check_source.py\n\n• Checkout source\n• Run checks\n• Accept/decline', fillcolor=bot_color)
        bots.node('other_bots', 'Other Bots\n\ne.g. check_bugowner.py', fillcolor=bot_color)

    # External systems
    with dot.subgraph(name='cluster_external') as ext:
        ext.attr(label='External Systems', style='dashed', color='gray', fontsize='12')
        ext.node('OBS', 'Open Build Service\n\nAPI', shape='cylinder', fillcolor='#F0F0F0')
        ext.node('Gitea', 'Gitea\n\nREST API v1', shape='cylinder', fillcolor='#F0F0F0')

    # Inheritance relationships
    dot.edge('SCMBase', 'SCM_OSC', label='implements', style='dashed', color='blue')
    dot.edge('SCMBase', 'SCM_Git', label='implements', style='dashed', color='blue')

    dot.edge('PlatformBase', 'Plat_OBS', label='implements', style='dashed', color='blue')
    dot.edge('PlatformBase', 'Plat_Gitea', label='implements', style='dashed', color='blue')

    # Usage relationships
    dot.edge('check_source', 'SCMBase', label='uses', color='green', style='bold')
    dot.edge('check_source', 'PlatformBase', label='uses', color='green', style='bold')
    dot.edge('other_bots', 'SCMBase', label='uses', color='green')
    dot.edge('other_bots', 'PlatformBase', label='uses', color='green')
    dot.edge('ReviewBot', 'check_source', label='extends', style='dashed', color='purple')
    dot.edge('ReviewBot', 'other_bots', label='extends', style='dashed', color='purple')

    # External connections
    dot.edge('SCM_OSC', 'OBS', label='osc.core', style='dotted', color='gray')
    dot.edge('Plat_OBS', 'OBS', label='osclib', style='dotted', color='gray')
    dot.edge('SCM_Git', 'Gitea', label='git clone', style='dotted', color='gray')
    dot.edge('Plat_Gitea', 'Gitea', label='REST', style='dotted', color='gray')

    # Add legend
    with dot.subgraph(name='cluster_legend') as legend:
        legend.attr(label='Legend', style='filled', color='#F8F8F8', fontsize='10')
        legend.node('leg_base', 'Abstract Base Class', fillcolor=base_color, fontsize='9')
        legend.node('leg_impl', 'Implementation', fillcolor=impl_color, fontsize='9')
        legend.node('leg_bot', 'Bot', fillcolor=bot_color, fontsize='9')
        legend.edge('leg_base', 'leg_impl', label='implements', style='dashed', color='blue', fontsize='8')
        legend.edge('leg_impl', 'leg_bot', label='uses', color='green', fontsize='8')

    return dot


if __name__ == '__main__':
    diagram = create_architecture_diagram()

    # Save as PNG
    diagram.render('scm-platform-architecture', format='png', cleanup=True, directory='.')
    print("Diagram saved as scm-platform-architecture.png")

    # Also save as SVG for scalability
    diagram.render('scm-platform-architecture', format='svg', cleanup=True, directory='.')
    print("Diagram saved as scm-platform-architecture.svg")
