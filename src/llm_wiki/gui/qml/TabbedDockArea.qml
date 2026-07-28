import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A dock area holding one or more panels, tabbed together when there's more
// than one. QML has no built-in QDockWidget-style drag/float/nest behavior
// (see IMPLEMENTATION_PLAN.md Phase 15) -- this is the deliberately simpler
// SplitView + TabBar replacement.
Item {
    id: root
    default property list<Item> panels

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        TabBar {
            id: tabBar
            Layout.fillWidth: true
            visible: root.panels.length > 1

            Repeater {
                model: root.panels.length
                TabButton {
                    text: root.panels[index].title
                }
            }
        }

        StackLayout {
            id: stack
            Layout.fillWidth: true
            Layout.fillHeight: true
            currentIndex: tabBar.currentIndex
        }
    }

    Component.onCompleted: {
        for (let i = 0; i < panels.length; i++) {
            panels[i].parent = stack
        }
    }
}
