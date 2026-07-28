import QtQuick
import QtQuick.Controls

Item {
    property string title: qsTr("Pipeline Log")

    Label {
        anchors.centerIn: parent
        text: qsTr("Live pipeline log -- wired up in Phase 15b")
        opacity: 0.6
    }
}
